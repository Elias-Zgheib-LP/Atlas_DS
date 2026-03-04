"""
CG-IDF v2 — LangGraph Pipeline Definition

Graph topology:
                          ┌──────────────────┐
                          │   provider_a     │  (Step 2 — LLM analysis)
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │  rules_engine    │  (Step 3 — deterministic)
                          └────────┬─────────┘
                                   │
                         [conditional edge]
                        /                    \
               review_queue                review_queue
               non-empty                   empty
                  │                           │
         ┌────────▼──────┐                    │
         │  provider_b   │  (Step 4 — LLM)   │
         └────────┬──────┘                    │
                  │                           │
                  └──────────┬────────────────┘
                             │
                    ┌────────▼─────────┐
                    │  merge_scoring   │  (Step 5 — deterministic)
                    └──────────────────┘
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, StateGraph

from cg_idf_v2.nodes.merge_scoring import run_merge_scoring
from cg_idf_v2.nodes.provider_a import run_provider_a
from cg_idf_v2.nodes.provider_b import run_provider_b
from cg_idf_v2.nodes.rules_engine import run_rules_engine, should_run_provider_b
from cg_idf_v2.schema import AuditState

# ---------------------------------------------------------------------------
# LangGraph requires a plain dict state, not a Pydantic model.
# We wrap each node so it converts AuditState <-> dict transparently.
# ---------------------------------------------------------------------------

def _wrap(node_fn):
    """
    Adapter: LangGraph passes state as a dict.
    We deserialize to AuditState, call the node, then re-serialize.
    """
    def wrapped(state_dict: Dict[str, Any]) -> Dict[str, Any]:
        state = AuditState.model_validate(state_dict)
        updated = node_fn(state)
        return updated.to_dict()
    wrapped.__name__ = node_fn.__name__
    return wrapped


def _route(state_dict: Dict[str, Any]) -> str:
    """Conditional edge: deserialize and delegate to the routing function."""
    state = AuditState.model_validate(state_dict)
    return should_run_provider_b(state)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    """
    Construct and compile the CG-IDF v2 LangGraph pipeline.

    Returns a compiled LangGraph graph ready for .invoke() / .stream().
    """
    # Use dict as the native state type (LangGraph requirement for simple graphs)
    graph = StateGraph(dict)

    # --- Nodes ---
    graph.add_node("provider_a",   _wrap(run_provider_a))
    graph.add_node("rules_engine", _wrap(run_rules_engine))
    graph.add_node("provider_b",   _wrap(run_provider_b))
    graph.add_node("merge_scoring",_wrap(run_merge_scoring))

    # --- Edges ---
    graph.set_entry_point("provider_a")
    graph.add_edge("provider_a", "rules_engine")

    # Conditional: go to provider_b only if review_queue is non-empty
    graph.add_conditional_edges(
        "rules_engine",
        _route,
        {
            "provider_b":    "provider_b",
            "merge_scoring": "merge_scoring",
        },
    )

    graph.add_edge("provider_b",    "merge_scoring")
    graph.add_edge("merge_scoring", END)

    return graph.compile()


# Singleton compiled graph — import and call directly in production
compiled_graph = build_graph()
