"""Pipeline-level tests for parallel sub-query retrieval after decompose.

`decompose` node emits N sub-queries. The retrieve node MUST fire N
parallel `hybrid_search` calls (one per sub-query) and RRF-merge the
results — instead of joining sub-queries with `" | "` into a single
embedding (which averages across topics and hurts recall on each one).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.services.multi_query_expansion import rrf_merge_chunks
from ragbot.shared.constants import DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY


# --------------------------------------------------------------------------- #
# Fakes (mirror multi_query_pipeline_integration test, kept self-contained)   #
# --------------------------------------------------------------------------- #
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _RecordingVectorStore:
    """Captures each hybrid_search call so tests assert call count + per-branch top_k."""

    def __init__(self):
        self.calls: list[dict] = []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id,
        top_k: int,
        **kw,
    ) -> list[dict]:
        self.calls.append({"query_text": query_text, "top_k": top_k})
        cid = f"chunk-{len(self.calls)}"
        return [
            {
                "chunk_id": cid,
                "text": f"hit for {query_text[:40]}",
                "content": f"hit for {query_text[:40]}",
                "score": 0.5,
            }
        ]

    async def search(self, **_kw):  # pragma: no cover — fallback path
        return []


class _FakeEmbedder:
    async def embed(self, texts, **_kw):
        if isinstance(texts, list):
            return [[0.1] * 8 for _ in texts]
        return [[0.1] * 8]

    async def embed_batch(self, texts, **_kw):
        return [[0.1] * 8 for _ in texts]


def _resolver_llm():
    """Resolver + LLM mock with structured-output decompose response."""
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw):
        purpose = kw.get("purpose", "")
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if purpose == "decompose":
            # Three atomic sub-questions for a comparison query.
            return {
                "text": '{"sub_queries": ["giá A", "giá B", "giá C"]}',
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        if "phân loại intent" in joined or purpose == "understand":
            user_q = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"),
                "",
            )
            # Force multi_hop intent so router sends to decompose.
            return {
                "text": '{"query": "' + user_q + '", "intent": "multi_hop"}',
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            return {
                "text": "Chunk 1: relevant",
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        return {
            "text": "Answer text.",
            "prompt_tokens": 1, "completion_tokens": 1,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(query: str, *, sub_queries: list[str] | None = None):
    state = {
        "tenant_id": uuid4(),
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "query": query,
        "rewritten_query": None,
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "pipeline_config": {
            # Disable multi_query so test isolates decompose vs single-retrieve.
            "multi_query_enabled": False,
            "decompose_enabled": True,
            "merge_condense_router": True,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": "mock/model",
            "embedding_dimension": 8,
            "top_k": 20,
            "decompose_top_k_per_subquery": DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY,
            "reranker_enabled": False,
            "rag_rrf_k": 60,
        },
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}
    if sub_queries is not None:
        state["sub_queries"] = sub_queries
    return state


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_top_k_per_subquery_recall_safety_default():
    """Constant default contract: chunks retrieved per sub-query branch.

    Raised 5→12 (recall-safety margin for multi-entity comparison/aggregation
    sub-queries — e.g. a "compare A vs B" decomposition needs both entities'
    chunks to survive into the post-RRF/rerank keep-set; 5/branch under-recalled
    one side). Downstream cliff + rerank trim the union back down, so the bump
    grows candidate recall without inflating the generation context.
    """
    assert DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY == 12


def test_no_subqueries_uses_single_retrieve():
    """No `sub_queries` in state ⇒ retrieve fires exactly 1 hybrid_search."""
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    state = _base_state("câu hỏi đơn", sub_queries=None)
    # Force factoid intent path so decompose is NOT triggered.
    state["pipeline_config"]["decompose_enabled"] = False

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert len(vs.calls) == 1, f"empty sub_queries must use single retrieve, got {vs.calls}"


def test_three_subqueries_fires_three_parallel_retrieves():
    """3 sub-queries pre-seeded ⇒ 3 hybrid_search calls, one per sub-query."""
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm()
    vs = _RecordingVectorStore()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    # Pre-seed sub_queries so test isolates retrieve behavior from decompose-LLM mock.
    state = _base_state(
        "so sánh giá A B C",
        sub_queries=["giá dịch vụ A", "giá dịch vụ B", "giá dịch vụ C"],
    )
    # Skip decompose node — sub_queries already in state.
    state["pipeline_config"]["decompose_enabled"] = False

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert len(vs.calls) == 3, f"expected 3 parallel hybrid_search, got {len(vs.calls)}: {vs.calls}"
    queries_seen = [c["query_text"] for c in vs.calls]
    assert any("giá dịch vụ A" in q for q in queries_seen), queries_seen
    assert any("giá dịch vụ B" in q for q in queries_seen), queries_seen
    assert any("giá dịch vụ C" in q for q in queries_seen), queries_seen
    # Each branch must use the per-sub-query top_k, not the global top_k=20.
    assert all(c["top_k"] == DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY for c in vs.calls), vs.calls


def test_rrf_merge_dedups_overlapping_chunks_across_subqueries():
    """RRF merge consolidates chunks appearing in multiple sub-query results."""
    list_a = [
        {"chunk_id": "c1", "text": "shared", "score": 0.9},
        {"chunk_id": "c2", "text": "only-a", "score": 0.7},
    ]
    list_b = [
        {"chunk_id": "c1", "text": "shared", "score": 0.85},
        {"chunk_id": "c3", "text": "only-b", "score": 0.6},
    ]
    list_c = [
        {"chunk_id": "c4", "text": "only-c", "score": 0.5},
    ]

    merged = rrf_merge_chunks([list_a, list_b, list_c], rrf_k=60)
    ids = [c["chunk_id"] for c in merged]

    assert len(ids) == 4, f"4 unique chunk_ids expected, got {ids}"
    assert ids.count("c1") == 1, "c1 must be deduped to a single entry"
    # c1 ranked first in 2 of 3 lists → top RRF score.
    assert ids[0] == "c1", f"shared chunk should rank highest, got order {ids}"
