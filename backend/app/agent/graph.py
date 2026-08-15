"""
Builds the compiled LangGraph state graph for the research agent.

This module only wires nodes together — all actual work happens inside
`agent/nodes.py`. Keeping graph construction separate from node logic
means the graph shape (and therefore the whole agentic control flow) is
visible in one place.
"""
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import Nodes
from app.agent.state import AgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


def _route_after_step(state: AgentState) -> str:
    """Shared routing: if a node recorded an error, go straight to failure handling."""
    return "handle_failure" if state.get("error") else "continue"


def _route_after_evaluate(state: AgentState) -> str:
    if state.get("error"):
        return "handle_failure"
    return state.get("next_action", "synthesize")


def build_graph(nodes: Nodes):
    """
    Construct and compile the research agent's StateGraph.

    Graph shape:

        analyze_and_plan -> search -> scrape_and_chunk -> retrieve_and_rank
            -> evaluate_evidence --(search_more)--> search            [loop]
                               --(synthesize)-----> synthesize -> validate_citations -> finalize
                               --(insufficient)---> finalize
        any node error -> handle_failure -> END
    """
    graph = StateGraph(AgentState)

    graph.add_node("analyze_and_plan", nodes.analyze_and_plan)
    graph.add_node("search", nodes.search)
    graph.add_node("scrape_and_chunk", nodes.scrape_and_chunk)
    graph.add_node("retrieve_and_rank", nodes.retrieve_and_rank)
    graph.add_node("evaluate_evidence", nodes.evaluate_evidence)
    graph.add_node("synthesize", nodes.synthesize)
    graph.add_node("validate_citations", nodes.validate_citations)
    graph.add_node("finalize", nodes.finalize)
    graph.add_node("handle_failure", nodes.handle_failure)

    graph.add_edge(START, "analyze_and_plan")

    graph.add_conditional_edges(
        "analyze_and_plan", _route_after_step, {"continue": "search", "handle_failure": "handle_failure"}
    )
    graph.add_conditional_edges(
        "search", _route_after_step,
        {"continue": "scrape_and_chunk", "handle_failure": "handle_failure"},
    )
    graph.add_conditional_edges(
        "scrape_and_chunk", _route_after_step,
        {"continue": "retrieve_and_rank", "handle_failure": "handle_failure"},
    )
    graph.add_conditional_edges(
        "retrieve_and_rank", _route_after_step,
        {"continue": "evaluate_evidence", "handle_failure": "handle_failure"},
    )

    # This is the agentic branch point: loop back to `search`, proceed to
    # synthesis, or stop early with an explicit insufficient-evidence path.
    graph.add_conditional_edges(
        "evaluate_evidence",
        _route_after_evaluate,
        {
            "search_more": "search",
            "synthesize": "synthesize",
            "insufficient": "finalize",
            "handle_failure": "handle_failure",
        },
    )

    graph.add_conditional_edges(
        "synthesize", _route_after_step,
        {"continue": "validate_citations", "handle_failure": "handle_failure"},
    )
    graph.add_edge("validate_citations", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("handle_failure", END)

    return graph.compile()
