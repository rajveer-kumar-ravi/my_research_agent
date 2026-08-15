"""
Research orchestration service.

This is the ONLY module that knows both about the LangGraph agent AND the
database. FastAPI routes call `ResearchService`, never the graph directly —
this keeps API code free of LangGraph internals per the project's layering
rules.

The graph is run with `astream()` (not a single `ainvoke()`) specifically
so we can persist progress/status to the database after every node
completes — this is what lets the frontend poll `/api/research/{id}` and
see live stage-by-stage progress instead of a single blocking call.
"""
import json
import time
from typing import Optional

from app.agent.graph import build_graph
from app.agent.nodes import Nodes
from app.agent.state import AgentState, new_initial_state
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import db_session
from app.db.repository import ResearchRepository
from app.db.redis_client import redis_db  # --- REDIS IMPORT ADDED ---
from app.models.research import ResearchStatus
from app.schemas.research import ProgressStage
from app.services.embedding_service import get_embedding_service
from app.services.gemini_service import GeminiService
from app.services.scraper_service import ScraperService
from app.services.search_service import get_search_provider

logger = get_logger(__name__)


class ResearchService:
    """
    Orchestrates a single research run end-to-end: builds the agent graph
    with real services, streams it to completion, and persists progress
    and the final report via the repository.
    """

    def __init__(self):
        self._settings = get_settings()

    def _build_nodes(self) -> Nodes:
        return Nodes(
            search_provider=get_search_provider(),
            scraper=ScraperService(),
            embedder=get_embedding_service(),
            gemini=GeminiService(),
        )

    async def run_research(self, research_id: str, query: str) -> None:
        """
        Execute the full research workflow for an already-created record and
        persist progress/results as it goes. Designed to be scheduled as a
        FastAPI background task — it manages its own DB session since it
        runs outside the request/response cycle.
        """
        start_time = time.time()
        
        # --- REDIS CACHE CHECK START ---
        # Query ke basis par ek unique cache key banate hain
        cache_key = f"research_cache:{query.strip().lower()}"
        
        if redis_db:
            try:
                cached_data = redis_db.get(cache_key)
                if cached_data:
                    logger.info("[%s] Cache hit! Returning data from Redis for query: '%s'", research_id, query)
                    report = json.loads(cached_data)
                    duration = time.time() - start_time
                    sources_count = len(report.get("sources", []))
                    
                    with db_session() as db:
                        repo = ResearchRepository(db)
                        repo.update_status(research_id, ResearchStatus.IN_PROGRESS)
                        
                        # UI ko batane ke liye ki data cache se load hua hai
                        cache_progress = [ProgressStage(stage="Cache", status="completed", detail="Loaded instantly from Redis cache")]
                        repo.update_progress(research_id, cache_progress)
                        
                        repo.save_report(
                            research_id,
                            report_json=json.dumps(report),
                            sources_count=sources_count,
                            duration_seconds=duration,
                        )
                    logger.info("[%s] Research completed from cache in %.1fs", research_id, duration)
                    return # Graph run skip kar dein kyunki data mil gaya
            except Exception as e:
                logger.error("[%s] Redis cache read error: %s", research_id, e)
        # --- REDIS CACHE CHECK END ---

        nodes = self._build_nodes()
        graph = build_graph(nodes)

        settings = self._settings
        initial_state = new_initial_state(
            research_id=research_id,
            query=query,
            max_iterations=3,
            max_search_queries=settings.max_urls_to_scrape // 2 or 5,
            max_urls_to_scrape=settings.max_urls_to_scrape,
            timeout_seconds=120,
        )

        with db_session() as db:
            repo = ResearchRepository(db)
            repo.update_status(research_id, ResearchStatus.IN_PROGRESS)

        final_state: Optional[AgentState] = None
        try:
            async for step_state in graph.astream(initial_state, stream_mode="values"):
                final_state = step_state
                self._persist_progress(research_id, step_state)
        except Exception as exc:
            # A true crash (bug, unexpected exception) outside the graph's
            # own error handling — still fail gracefully, never silently.
            logger.error("[%s] Research run crashed: %s", research_id, exc)
            with db_session() as db:
                repo = ResearchRepository(db)
                repo.update_status(research_id, ResearchStatus.FAILED, error_message=str(exc))
            return

        if final_state is None:
            with db_session() as db:
                repo = ResearchRepository(db)
                repo.update_status(
                    research_id, ResearchStatus.FAILED, error_message="Agent produced no output."
                )
            return

        duration = time.time() - start_time

        with db_session() as db:
            repo = ResearchRepository(db)
            if final_state.get("status") == "failed" or final_state.get("error"):
                repo.update_status(
                    research_id,
                    ResearchStatus.FAILED,
                    error_message=final_state.get("error", "Unknown error"),
                )
                self._persist_progress(research_id, final_state, db_override=db)
                return

            report = final_state.get("report") or {}
            sources_count = len(report.get("sources", []))
            repo.save_report(
                research_id,
                report_json=json.dumps(report),
                sources_count=sources_count,
                duration_seconds=duration,
            )
            self._persist_progress(research_id, final_state, db_override=db)

            # --- REDIS CACHE SAVE START ---
            if redis_db and report:
                try:
                    # Naye result ko Redis me 24 hours (86400 seconds) ke liye cache karein
                    redis_db.setex(cache_key, 86400, json.dumps(report))
                    logger.info("[%s] Saved successful research result to Redis cache.", research_id)
                except Exception as e:
                    logger.error("[%s] Redis cache write error: %s", research_id, e)
            # --- REDIS CACHE SAVE END ---

        logger.info(
            "[%s] Research completed in %.1fs, %d source(s)",
            research_id, duration, len(final_state.get("sources", [])),
        )

    def _persist_progress(self, research_id: str, state: AgentState, db_override=None) -> None:
        progress = [
            ProgressStage(stage=p["stage"], status=p["status"], detail=p.get("detail"))
            for p in state.get("progress", [])
        ]
        if db_override is not None:
            ResearchRepository(db_override).update_progress(research_id, progress)
            return
        with db_session() as db:
            ResearchRepository(db).update_progress(research_id, progress)


_default_service: Optional[ResearchService] = None


def get_research_service() -> ResearchService:
    global _default_service
    if _default_service is None:
        _default_service = ResearchService()
    return _default_service