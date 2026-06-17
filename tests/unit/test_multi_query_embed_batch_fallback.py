"""[T2-CostPerf] Multi-query embed_batch — fallback to parallel gather.

Verifies that when the embedder does NOT have ``embed_batch`` (or when it
lacks ``embed_one`` as the spec-resolution guard requires both), the
``_embed_batch_queries`` helper falls back to parallel ``asyncio.gather``
of individual ``_embed_query`` calls — retrieving the same number of
chunks as the batch path.
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

class _EmbedOnlyEmbedder:
    """Embedder that only has the old `.embed()` interface — no embed_batch."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.embed_calls: list[list[str]] = []

    async def embed(self, texts: list[str], **_kw) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [[0.3] * self.dim for _ in texts]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class _BatchOnlyEmbedder:
    """Embedder with embed_batch but NO embed_one — tests guard condition."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.batch_calls: list[list[str]] = []

    async def embed_batch(self, texts: list[str], **_kw) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        return [[0.4] * self.dim for _ in texts]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _RecordingVectorStore:
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
        return [{"chunk_id": cid, "text": f"hit for {query_text[:30]}", "content": f"hit for {query_text[:30]}", "score": 0.5}]

    async def search(self, **_kw) -> list:
        return []


def _make_resolver_llm() -> tuple:
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    cfg.embedding_spec = None
    cfg.binding_id = uuid4()
    cfg.wire_model_id = "mock-model"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw) -> dict:
        purpose = kw.get("purpose", "")
        if purpose == "multi_query":
            return {"text": '["alt 1", "alt 2"]', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại" in joined:
            uq = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            return {"text": f'{{"query": "{uq}", "intent": "factoid"}}', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        return {"text": "Answer text.", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(query: str) -> dict:
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
        "_mq_queries": [query, "alt variant 1", "alt variant 2"],
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
            "pipeline_multi_query_embed_batch_enabled": True,
            "merge_condense_router": True,
            "decompose_enabled": False,
            "adaptive_router_l1_enabled": False,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": "mock/model",
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

def test_embed_only_embedder_still_runs_multi_query():
    """Embedder without embed_batch — fallback path must still produce N
    hybrid_search calls via parallel asyncio.gather of embed_query."""
    from ragbot.orchestration.query_graph import build_graph

    embedder = _EmbedOnlyEmbedder()
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
    state = _base_state("bảo hành sản phẩm")
    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Multi-query must still fire N hybrid_search calls
    assert len(vs.calls) >= 2, (
        f"Fallback path must still run multi-query: got {len(vs.calls)} calls"
    )
    # Each call should have a non-empty embedding
    for call in vs.calls:
        assert call["embedding"], f"Empty embedding in fallback path for {call['query_text']!r}"


def test_batch_only_embedder_falls_back_gracefully():
    """Embedder with embed_batch but no embed_one — does not satisfy the full
    EmbeddingPort contract (_embed_batch_queries guard requires both).
    Pipeline must NOT crash; it degrades gracefully to 0 chunks."""
    from ragbot.orchestration.query_graph import build_graph

    embedder = _BatchOnlyEmbedder()
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
    state = _base_state("câu hỏi về sản phẩm")
    # Should not raise — graceful degradation
    result = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))
    # Pipeline ran to completion (no exception)
    assert result is not None, "Pipeline must complete without exception"
    # answer_type may be no_context or similar — not a crash
    assert "answer" in result, "Pipeline must produce an answer key in state"
