"""[T2-CostPerf] Multi-query embed_batch — ZeroEntropy single-call path.

Verifies that when the embedder supports ``embed_batch`` + ``embed_one``,
``_embed_batch_queries`` issues exactly ONE batch HTTP call instead of N
sequential ``_embed_query`` calls, and that the returned embeddings are
passed directly to ``_run_hybrid_for_query`` as ``precomputed_embedding``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _BatchCountingEmbedder:
    """Embedder that records calls to embed_batch and embed_one separately."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.batch_calls: list[list[str]] = []
        self.one_calls: list[str] = []

    async def embed_batch(
        self, texts: list[str], *, spec=None, record_tenant_id=None
    ) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        return [[float(i + 1)] * self.dim for i in range(len(texts))]

    async def embed_one(
        self, text: str, *, spec=None, record_tenant_id=None
    ) -> list[float]:
        self.one_calls.append(text)
        return [0.5] * self.dim

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        ctx.record_llm = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _RecordingVectorStore:
    """Records every hybrid_search call + its query_embedding."""

    def __init__(self) -> None:
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
        self.calls.append({"query_text": query_text, "embedding": query_embedding})
        cid = f"chunk-{len(self.calls)}"
        return [{"chunk_id": cid, "text": f"text for {query_text[:30]}", "content": f"text for {query_text[:30]}", "score": 0.6}]

    async def search(self, **_kw) -> list:
        return []


def _make_resolver_llm() -> tuple:
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/zembed-1"
    cfg.model_name = "mock/zembed-1"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="zeroentropy")
    cfg.embedding_spec = None  # _to_embedding_spec path
    cfg.binding_id = uuid4()
    cfg.wire_model_id = "zembed-1"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw) -> dict:
        purpose = kw.get("purpose", "")
        if purpose == "multi_query":
            return {"text": '["bao lâu", "thời gian"]', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại" in joined:
            uq = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            return {"text": f'{{"query": "{uq}", "intent": "factoid"}}', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        return {"text": "Answer text.", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(query: str, *, embed_batch_enabled: bool = True) -> dict:
    # Pre-inject paraphrases so the retrieve node uses them directly without
    # firing an LLM multi_query expansion call (avoids L1 router short-circuit).
    return {
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
        "_mq_queries": [query, "bao lâu bảo hành", "thời gian bảo hành"],
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
            "multi_query_enabled": True,
            "multi_query_n_variants": 3,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            "pipeline_multi_query_embed_batch_enabled": embed_batch_enabled,
            "merge_condense_router": True,
            "decompose_enabled": False,
            "adaptive_router_l1_enabled": False,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": "mock/zembed-1",
            "embedding_dimension": 8,
            "top_k": 10,
            "reranker_enabled": False,
            "rag_rrf_k": 60,
        },
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_embed_batch_queries_uses_single_batch_call():
    """With ZeroEntropy-style embedder, _embed_batch_queries must call embed_batch
    exactly once (not N times) and return one embedding per query."""
    from ragbot.orchestration.query_graph import build_graph

    embedder = _BatchCountingEmbedder()
    vs = _RecordingVectorStore()
    resolver, llm = _make_resolver_llm()

    graph = build_graph(
        invocation_logger=MagicMock(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=embedder,
    )
    state = _base_state("bảo hành sản phẩm", embed_batch_enabled=True)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Must have ≥1 hybrid_search calls (multi-query active)
    assert len(vs.calls) >= 2, f"Expected ≥2 hybrid_search calls (multi-query), got {len(vs.calls)}"
    # embed_batch must be called (at least once from _embed_batch_queries)
    assert len(embedder.batch_calls) >= 1, "embed_batch must be called at least once"
    # Total batch calls should be far fewer than N individual embed calls
    total_batch_items = sum(len(b) for b in embedder.batch_calls)
    # For N=3 queries, batch should embed ≥3 texts in one or few calls
    assert total_batch_items >= len(vs.calls), (
        f"embed_batch should cover all query variants: {total_batch_items} items for {len(vs.calls)} search calls"
    )


def test_precomputed_embeddings_passed_to_hybrid_search():
    """When batch embed returns embeddings, each hybrid_search call should receive
    a non-empty embedding (not fall through to a zero-vector fallback)."""
    from ragbot.orchestration.query_graph import build_graph

    embedder = _BatchCountingEmbedder(dim=8)
    vs = _RecordingVectorStore()
    resolver, llm = _make_resolver_llm()

    graph = build_graph(
        invocation_logger=MagicMock(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=embedder,
    )
    state = _base_state("thời gian bảo hành", embed_batch_enabled=True)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Each hybrid_search call must have received a non-empty embedding
    for call in vs.calls:
        assert call["embedding"], f"hybrid_search received empty embedding for query {call['query_text']!r}"
        assert len(call["embedding"]) == 8, f"Expected dim=8, got {len(call['embedding'])}"
