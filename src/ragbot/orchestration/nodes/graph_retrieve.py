"""GraphRAG retrieval node — pulls extra context from the knowledge graph.

Extracted from ``query_graph.build_graph``. Per-request services (``kg_service``,
``session_factory``) are read from ``GraphState`` at execution time, so this node
captures nothing from the builder — it is a plain module-level function.
"""
from __future__ import annotations

from ragbot.infrastructure.graph.graph_retriever import graph_retrieve as _graph_retrieve
from ragbot.orchestration.state import GraphState


async def graph_retrieve_node(state: GraphState) -> dict:
    """Retrieve additional context via knowledge graph (GraphRAG); empty on any failure."""
    _kg = state.get("kg_service")
    _sf = state.get("session_factory")
    if _kg is None or _sf is None:
        return {"graph_context": []}

    async with state["step_tracker"].step("graph_retrieve"):
        return await _graph_retrieve(
            state,
            kg_service=_kg,
            session_factory=_sf,
        )
