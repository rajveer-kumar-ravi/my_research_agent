"""
LangGraph node implementations for the research agent.

Every node:
  * takes and returns an `AgentState` dict (LangGraph merges the returned
    partial dict into the running state);
  * NEVER raises for expected/operational failures — those are captured
    into `state["error"]` and routed to `handle_failure` by the graph's
    conditional edges, so a single bad search/scrape/LLM call fails the
    run cleanly instead of crashing the whole process;
  * accepts injected service instances (constructor-injected via `Nodes`)
    so the whole graph is trivially testable with mocks — no node ever
    reaches for a global/singleton service itself.

Untrusted content: scraped web page text is never treated as instructions
anywhere in this module or in gemini_service — see the prompt-construction
comments in gemini_service.synthesize for the explicit defense.
"""
import asyncio
import time
from dataclasses import asdict
from typing import List

from app.agent.state import SUFFICIENCY_SCORE_THRESHOLD, AgentState, add_progress
from app.core.logging import get_logger
from app.services.citation_service import CitationService
from app.services.embedding_service import EmbeddingService
from app.services.gemini_service import GeminiService, GeminiServiceError
from app.services.retrieval_service import RetrievalService, RetrievedChunk
from app.services.scraper_service import DocumentChunk, ScraperService
from app.services.search_service import SearchProvider, SearchProviderError, SearchResult
from app.services.source_quality_service import SourceQualityService

logger = get_logger(__name__)


class Nodes:
    """
    Holds the service dependencies used by every graph node. Constructed
    once per research run by `agent/graph.py` (or directly by tests with
    mocked services).
    """

    def __init__(
        self,
        search_provider: SearchProvider,
        scraper: ScraperService,
        embedder: EmbeddingService,
        gemini: GeminiService,
    ):
        self.search_provider = search_provider
        self.scraper = scraper
        self.retrieval = RetrievalService(embedding_service=embedder)
        self.gemini = gemini
        self.citations = CitationService(quality_service=SourceQualityService())

    # ---------------------------------------------------------------
    # 1. analyze_and_plan
    # ---------------------------------------------------------------
    async def analyze_and_plan(self, state: AgentState) -> AgentState:
        add_progress(state, "analyzing", "in_progress")
        add_progress(state, "planning", "pending")
        try:
            plan = await asyncio.to_thread(self.gemini.analyze_and_plan, state["query"])
        except GeminiServiceError as exc:
            logger.error("[%s] Planning failed: %s", state["research_id"], exc)
            add_progress(state, "planning", "failed", str(exc))
            return {"error": f"Research planning failed: {exc}", "status": "failed"}

        pending_queries: List[str] = []
        for sub_q in plan.sub_questions:
            pending_queries.extend(sub_q.search_queries)
        # Always keep the raw user query as a fallback search query too.
        if not pending_queries:
            pending_queries = [state["query"]]

        add_progress(state, "analyzing", "completed")
        add_progress(state, "planning", "completed", f"{len(plan.sub_questions)} sub-question(s)")

        return {
            "plan": plan.model_dump(),
            "pending_queries": pending_queries,
            "status": "in_progress",
        }

    # ---------------------------------------------------------------
    # 2. search  (also performs "source selection": dedupe + rank + cap)
    # ---------------------------------------------------------------
    async def search(self, state: AgentState) -> AgentState:
        add_progress(state, "searching", "in_progress")

        pending = list(state.get("pending_queries", []))
        executed = list(state.get("executed_queries", []))
        seen_urls = set(state.get("seen_urls", []))
        remaining_query_budget = state["max_search_queries"] - len(executed)

        if remaining_query_budget <= 0 or not pending:
            # Nothing left to search with — not an error, just nothing new to add.
            add_progress(state, "searching", "completed", "No further queries to run.")
            return {}

        # Take a small batch of queries this iteration rather than the whole
        # queue at once, so the loop can re-evaluate evidence sufficiency
        # between batches instead of over-searching blindly.
        batch = pending[:2][:remaining_query_budget]
        remaining_pending = pending[len(batch):]

        all_results: List[SearchResult] = []
        try:
            for query in batch:
                results = await asyncio.to_thread(
                    self.search_provider.search, query, 5
                )
                all_results.extend(results)
                executed.append(query)
        except SearchProviderError as exc:
            logger.error("[%s] Search failed: %s", state["research_id"], exc)
            add_progress(state, "searching", "failed", str(exc))
            return {"error": f"Web search failed: {exc}", "status": "failed"}

        # --- Source selection: dedupe against everything seen so far, then
        # rank remaining candidates by search score and cap how many new
        # URLs we allow into the scrape budget this iteration. ---
        new_candidates = [r for r in all_results if r.url and r.url not in seen_urls]
        new_candidates.sort(key=lambda r: r.score, reverse=True)

        already_scraped = len(state.get("scraped_urls", []))
        scrape_budget = max(0, state["max_urls_to_scrape"] - already_scraped)
        selected = new_candidates[:scrape_budget]

        for r in selected:
            seen_urls.add(r.url)

        add_progress(
            state, "searching", "completed",
            f"{len(batch)} quer{'y' if len(batch)==1 else 'ies'}, {len(selected)} new source(s) selected",
        )

        return {
            "pending_queries": remaining_pending,
            "executed_queries": executed,
            "seen_urls": list(seen_urls),
            "_selected_for_scrape": [asdict_search_result(r) for r in selected],  # transient
        }

    # ---------------------------------------------------------------
    # 3. scrape_and_chunk
    # ---------------------------------------------------------------
    async def scrape_and_chunk(self, state: AgentState) -> AgentState:
        add_progress(state, "scraping", "in_progress")

        selected = state.get("_selected_for_scrape", [])  # set by `search`
        if not selected:
            add_progress(state, "scraping", "completed", "No new sources to scrape this round.")
            return {}

        urls_with_dates = [(item["url"], item.get("published_date")) for item in selected]

        try:
            scrape_results = await asyncio.to_thread(self.scraper.scrape_many, urls_with_dates)
        except Exception as exc:  # scraper_service itself never raises, but guard anyway
            logger.error("[%s] Scraping crashed unexpectedly: %s", state["research_id"], exc)
            add_progress(state, "scraping", "failed", str(exc))
            return {"error": f"Scraping failed unexpectedly: {exc}", "status": "failed"}

        new_chunks = list(state.get("chunks", []))
        scraped_urls = list(state.get("scraped_urls", []))
        success_count = 0

        for result in scrape_results:
            scraped_urls.append(result.url)
            if result.success:
                success_count += 1
                for chunk in result.chunks:
                    new_chunks.append(asdict(chunk))
            else:
                logger.info("[%s] Scrape failed for %s: %s", state["research_id"], result.url, result.error)

        add_progress(
            state, "scraping", "completed",
            f"{success_count}/{len(scrape_results)} page(s) scraped successfully",
        )

        return {"chunks": new_chunks, "scraped_urls": scraped_urls}

    # ---------------------------------------------------------------
    # 4. retrieve_and_rank
    # ---------------------------------------------------------------
    async def retrieve_and_rank(self, state: AgentState) -> AgentState:
        add_progress(state, "retrieving", "in_progress")

        chunk_dicts = state.get("chunks", [])
        if not chunk_dicts:
            add_progress(state, "retrieving", "completed", "No evidence available yet.")
            return {"retrieved": []}

        chunks = [DocumentChunk(**c) for c in chunk_dicts]

        try:
            retrieved = await asyncio.to_thread(self.retrieval.retrieve, state["query"], chunks)
        except Exception as exc:
            logger.error("[%s] Retrieval failed: %s", state["research_id"], exc)
            add_progress(state, "retrieving", "failed", str(exc))
            return {"error": f"Evidence retrieval failed: {exc}", "status": "failed"}

        add_progress(state, "retrieving", "completed", f"{len(retrieved)} chunk(s) ranked")
        return {"retrieved": [asdict(r) for r in retrieved]}

    # ---------------------------------------------------------------
    # 5. evaluate_evidence  — THE AGENTIC DECISION POINT
    # ---------------------------------------------------------------
    async def evaluate_evidence(self, state: AgentState) -> AgentState:
        add_progress(state, "verifying", "in_progress")

        iteration = state.get("iteration", 0)
        elapsed = time.time() - state["started_at"]
        retrieved = state.get("retrieved", [])
        top_score = retrieved[0]["final_score"] if retrieved else 0.0

        budget_exhausted = (
            iteration >= state["max_iterations"] - 1
            or len(state.get("executed_queries", [])) >= state["max_search_queries"]
            or elapsed >= state["timeout_seconds"]
        )

        if budget_exhausted:
            next_action = "synthesize" if retrieved else "insufficient"
            reason = None if retrieved else "Research budget exhausted with no usable evidence."
        elif not retrieved:
            next_action = "search_more"
            reason = None
        elif top_score < SUFFICIENCY_SCORE_THRESHOLD:
            next_action = "search_more"
            reason = None
        else:
            next_action = "synthesize"
            reason = None

        # If we're going to search again but the plan has no more queries
        # queued, ask Gemini for one targeted refinement query instead of
        # giving up — this is the "retry with a better query" behavior.
        pending_queries = list(state.get("pending_queries", []))
        if next_action == "search_more" and not pending_queries:
            try:
                refined = await asyncio.to_thread(
                    self.gemini.suggest_refinement_query,
                    state["query"],
                    state.get("executed_queries", []),
                )
                pending_queries = [refined]
            except GeminiServiceError:
                # If even the refinement call fails, don't loop forever — stop here.
                next_action = "synthesize" if retrieved else "insufficient"

        add_progress(
            state, "verifying", "completed",
            f"decision={next_action}, top_score={top_score:.3f}, iteration={iteration}",
        )

        result: AgentState = {
            "next_action": next_action,
            "evidence_sufficient": next_action != "insufficient",
            "insufficiency_reason": reason,
        }
        if next_action == "search_more":
            result["iteration"] = iteration + 1
            result["pending_queries"] = pending_queries
        return result

    # ---------------------------------------------------------------
    # 6. synthesize
    # ---------------------------------------------------------------
    async def synthesize(self, state: AgentState) -> AgentState:
        add_progress(state, "synthesizing", "in_progress")

        retrieved = [RetrievedChunk(**r) for r in state.get("retrieved", [])]
        try:
            synthesis = await asyncio.to_thread(self.gemini.synthesize, state["query"], retrieved)
        except GeminiServiceError as exc:
            logger.error("[%s] Synthesis failed: %s", state["research_id"], exc)
            add_progress(state, "synthesizing", "failed", str(exc))
            return {"error": f"Report synthesis failed: {exc}", "status": "failed"}

        add_progress(state, "synthesizing", "completed")
        return {"synthesis": synthesis.model_dump()}

    # ---------------------------------------------------------------
    # 7. validate_citations
    # ---------------------------------------------------------------
    async def validate_citations(self, state: AgentState) -> AgentState:
        retrieved = [RetrievedChunk(**r) for r in state.get("retrieved", [])]
        synthesis = state.get("synthesis") or {}
        raw_claims = synthesis.get("claims", [])

        check = self.citations.validate_claims(raw_claims, retrieved)
        sources = self.citations.build_sources(retrieved)

        return {
            "claims": [c.model_dump() for c in check.claims],
            "sources": [s.model_dump() for s in sources],
            "dropped_citation_count": check.dropped_citation_count,
        }

    # ---------------------------------------------------------------
    # 8. finalize
    # ---------------------------------------------------------------
    async def finalize(self, state: AgentState) -> AgentState:
        if not state.get("evidence_sufficient", True):
            report = {
                "executive_summary": (
                    "Research could not be completed with sufficient evidence."
                ),
                "key_findings": [],
                "detailed_analysis": "",
                "comparison_table": [],
                "claims": [],
                "conflicts": [],
                "sources": [s for s in state.get("sources", [])],
                "evidence_sufficient": False,
                "insufficient_evidence_note": state.get("insufficiency_reason")
                or "Insufficient evidence was found to responsibly answer this question.",
            }
            add_progress(state, "completed", "completed", "Insufficient evidence")
            return {"report": report, "status": "completed"}

        synthesis = state.get("synthesis") or {}
        report = {
            "executive_summary": synthesis.get("executive_summary", ""),
            "key_findings": synthesis.get("key_findings", []),
            "detailed_analysis": synthesis.get("detailed_analysis", ""),
            "comparison_table": synthesis.get("comparison_table", []),
            "claims": state.get("claims", []),
            "conflicts": synthesis.get("conflicts", []),
            "sources": state.get("sources", []),
            "evidence_sufficient": synthesis.get("evidence_sufficient", True),
            "insufficient_evidence_note": synthesis.get("insufficient_evidence_note"),
        }
        add_progress(state, "completed", "completed")
        return {"report": report, "status": "completed"}

    # ---------------------------------------------------------------
    # handle_failure
    # ---------------------------------------------------------------
    async def handle_failure(self, state: AgentState) -> AgentState:
        logger.error("[%s] Research run failed: %s", state.get("research_id"), state.get("error"))
        return {"status": "failed"}


def asdict_search_result(result: SearchResult) -> dict:
    return {
        "url": result.url,
        "title": result.title,
        "snippet": result.snippet,
        "score": result.score,
        "published_date": result.published_date,
    }
