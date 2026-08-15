"""
Typed state for the research agent's LangGraph workflow.

Everything here is a plain, JSON-serializable dict/list/primitive (not
live service objects) so the state can be safely logged, persisted, tested
with plain dicts, and streamed. Node functions reconstruct typed
dataclasses/Pydantic objects from these dicts only for the duration of a
single node call.
"""
import time
from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # ---- Immutable run configuration (set once at graph start) ----
    research_id: str
    query: str
    max_iterations: int
    max_search_queries: int
    max_urls_to_scrape: int
    timeout_seconds: int
    started_at: float

    # ---- Planning (Stage: query analysis / planning / decomposition) ----
    plan: Optional[Dict[str, Any]]          # serialized ResearchPlan
    pending_queries: List[str]              # queue of not-yet-executed search queries
    executed_queries: List[str]             # all search queries executed so far

    # ---- Search + source selection + scraping + chunking ----
    seen_urls: List[str]                    # every URL ever surfaced by search (dedup)
    scraped_urls: List[str]                 # URLs actually scraped (success or fail)
    chunks: List[Dict[str, Any]]            # accumulated DocumentChunk dicts, all iterations
    _selected_for_scrape: List[Dict[str, Any]]  # transient: this iteration's chosen URLs

    # ---- Retrieval / ranking ----
    retrieved: List[Dict[str, Any]]         # latest RetrievedChunk dicts, ranked

    # ---- Agentic loop control ----
    iteration: int
    next_action: str                        # "search_more" | "synthesize" | "insufficient"
    evidence_sufficient: bool
    insufficiency_reason: Optional[str]

    # ---- Synthesis / citation validation ----
    synthesis: Optional[Dict[str, Any]]     # serialized GeminiSynthesis
    claims: List[Dict[str, Any]]            # validated Claim dicts
    sources: List[Dict[str, Any]]           # validated Source dicts
    dropped_citation_count: int

    # ---- Output / status ----
    report: Optional[Dict[str, Any]]        # serialized ResearchReport
    status: str                             # mirrors ResearchStatus values
    error: Optional[str]
    progress: List[Dict[str, str]]          # list of {stage, status, detail}


# Stages shown to the frontend, in the order they normally occur.
PIPELINE_STAGES = [
    "analyzing",
    "planning",
    "searching",
    "scraping",
    "retrieving",
    "verifying",
    "synthesizing",
    "completed",
]

SUFFICIENCY_SCORE_THRESHOLD = 0.35


def new_initial_state(
    research_id: str,
    query: str,
    max_iterations: int = 3,
    max_search_queries: int = 10,
    max_urls_to_scrape: int = 12,
    timeout_seconds: int = 180,
) -> AgentState:
    """Build the initial state for a fresh research run."""
    return AgentState(
        research_id=research_id,
        query=query,
        max_iterations=max_iterations,
        max_search_queries=max_search_queries,
        max_urls_to_scrape=max_urls_to_scrape,
        timeout_seconds=timeout_seconds,
        started_at=time.time(),
        plan=None,
        pending_queries=[],
        executed_queries=[],
        seen_urls=[],
        scraped_urls=[],
        chunks=[],
        retrieved=[],
        iteration=0,
        next_action="",
        evidence_sufficient=True,
        insufficiency_reason=None,
        synthesis=None,
        claims=[],
        sources=[],
        dropped_citation_count=0,
        report=None,
        status="pending",
        error=None,
        progress=[],
    )


def add_progress(state: AgentState, stage: str, status: str, detail: Optional[str] = None) -> None:
    """Append a progress entry in place. Mutating helper used by every node."""
    entries = state.setdefault("progress", [])
    entries.append({"stage": stage, "status": status, "detail": detail or ""})
