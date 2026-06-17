"""Unit tests for `graph_retrieve_node` orchestration node.

Node lives at `query_graph.py:3056` and delegates to
`infrastructure/graph/graph_retriever.graph_retrieve`. Critical paths:

- `kg_service is None` → `{"graph_context": []}` no DB touch
- `session_factory is None` → `{"graph_context": []}` no DB touch
- happy path: kg_service.query_graph called, returns chunks
- `_retrieve_route` selects "graph_retrieve" or "rerank" based on
  `graph_rag_mode` + `intent`
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_state,
    node_callable,
)


def _afunc(compiled):
    return node_callable(compiled, "graph_retrieve")


def test_graph_retrieve_returns_empty_when_kg_service_none():
    compiled, tracker, *_ = build_test_graph(
        kg_service=None, session_factory=MagicMock()
    )
    state = make_state(pipeline_config={"graph_rag_mode": "always"})
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {"graph_context": []}
    # Early-exit must not even open the step tracker.
    assert tracker.by_name("graph_retrieve") == []


def test_graph_retrieve_returns_empty_when_session_factory_none():
    compiled, tracker, *_ = build_test_graph(
        kg_service=MagicMock(), session_factory=None
    )
    state = make_state(pipeline_config={"graph_rag_mode": "always"})
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {"graph_context": []}
    assert tracker.by_name("graph_retrieve") == []


def test_graph_retrieve_disabled_mode_short_circuits_inside_helper():
    """Even if both deps are wired, graph_rag_mode='disabled' returns []."""
    kg = MagicMock()
    kg.query_graph = AsyncMock(return_value=[])

    @asynccontextmanager
    async def _factory():
        yield MagicMock()

    compiled, tracker, *_ = build_test_graph(
        kg_service=kg, session_factory=_factory
    )
    state = make_state(pipeline_config={"graph_rag_mode": "disabled"})
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {"graph_context": []}
    # The node still wraps when both deps are present (the helper itself
    # is what bails based on mode). Tracker count: 1.
    assert len(tracker.by_name("graph_retrieve")) == 1
    # And kg.query_graph must NOT have been hit (mode short-circuited).
    assert kg.query_graph.await_count == 0


def test_graph_retrieve_adaptive_mode_skips_factoid_intent():
    kg = MagicMock()
    kg.query_graph = AsyncMock(return_value=[])

    @asynccontextmanager
    async def _factory():
        yield MagicMock()

    compiled, *_ = build_test_graph(
        kg_service=kg, session_factory=_factory
    )
    state = make_state(
        intent="factoid",
        pipeline_config={"graph_rag_mode": "adaptive"},
    )
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {"graph_context": []}
    assert kg.query_graph.await_count == 0


def test_graph_retrieve_adaptive_mode_runs_for_multi_hop_intent():
    kg = MagicMock()
    # Return one synthetic triple-derived chunk to confirm propagation.
    kg.query_graph = AsyncMock(
        return_value=[
            {
                "chunk_id": "kg-1",
                "content": "edge: A → B (relation: foo)",
                "score": 0.42,
            }
        ]
    )

    @asynccontextmanager
    async def _factory():
        yield MagicMock()

    compiled, tracker, *_ = build_test_graph(
        kg_service=kg, session_factory=_factory
    )
    state = make_state(
        intent="multi_hop",
        pipeline_config={"graph_rag_mode": "adaptive"},
    )
    out = asyncio.run(_afunc(compiled)(state))
    # Step DID wrap and helper DID call kg.query_graph for multi_hop.
    assert len(tracker.by_name("graph_retrieve")) == 1
    assert kg.query_graph.await_count == 1
    # graph_context payload must be a list (may be wrapped by helper).
    assert "graph_context" in out
    assert isinstance(out["graph_context"], list)


def test_retrieve_route_disabled_returns_rerank():
    """`_retrieve_route` is a private branch fn; emulate via build_graph wiring."""
    # We can't import _retrieve_route directly (closure), but we can verify
    # the wiring by checking the conditional edges of the compiled graph.
    compiled, *_ = build_test_graph()
    # Edges from "retrieve" must include "rerank" and "graph_retrieve" as
    # destinations — confirms the conditional router is hooked up.
    edges = list(getattr(compiled, "get_graph", lambda: None)().edges) if hasattr(
        compiled, "get_graph"
    ) else []
    edge_targets = {(e.source, e.target) for e in edges}
    has_rerank = any(src == "retrieve" and tgt == "rerank" for src, tgt in edge_targets)
    has_graph = any(
        src == "retrieve" and tgt == "graph_retrieve" for src, tgt in edge_targets
    )
    assert has_rerank, edge_targets
    assert has_graph, edge_targets


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
