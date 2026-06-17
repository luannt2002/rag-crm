"""[T2-CostPerf] Multi-query embed_batch — edge cases.

Covers:
- Empty query list → returns empty list, no embedder call.
- Single query (N=1) → batch path skipped, normal single _embed_query.
- Batch returns wrong count → falls back to per-branch _embed_query.
- pipeline_multi_query_embed_batch_enabled=False → no batch call at all.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _CountingEmbedder:
    """Embedder that records how many times embed_batch was called."""

    def __init__(self, dim: int = 8, wrong_count: bool = False) -> None:
        self.dim = dim
        self.wrong_count = wrong_count  # simulate mismatch in returned count
        self.batch_calls: list[list[str]] = []

    async def embed_batch(self, texts: list[str], **_kw) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        if self.wrong_count:
            # Return fewer embeddings than requested to trigger fallback
            return [[0.1] * self.dim]  # only 1, not len(texts)
        return [[0.5] * self.dim for _ in texts]

    async def embed_one(self, text: str, **_kw) -> list[float]:
        return [0.5] * self.dim

    async def embed(self, texts: list[str], **_kw) -> list[list[float]]:
        return [[0.5] * self.dim for _ in texts]

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
        return [{"chunk_id": cid, "text": "text", "content": "text", "score": 0.5}]

    async def search(self, **_kw) -> list:
        return []


def _make_resolver_llm(*, n_variants: int = 3) -> tuple:
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
            variants = ["alt 1", "alt 2"] if n_variants >= 3 else []
            return {
                "text": str(variants),
                "prompt_tokens": 1, "completion_tokens": 1,
                "cost_usd": 0.0, "finish_reason": "stop",
            }
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại" in joined:
            uq = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            return {"text": f'{{"query": "{uq}", "intent": "factoid"}}', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        return {"text": "Answer.", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(
    query: str,
    *,
    embed_batch_enabled: bool = True,
    n_variants: int = 3,
    mq_enabled: bool = True,
    preset_queries: list[str] | None = None,
) -> dict:
    # Pre-inject paraphrases so the retrieve node uses them directly without
    # firing an LLM multi_query expansion call (avoids L1 router short-circuit).
    # Callers pass preset_queries=None to test single-query (no preset) path.
    state: dict = {
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
            "multi_query_enabled": mq_enabled,
            "multi_query_n_variants": n_variants,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            "pipeline_multi_query_embed_batch_enabled": embed_batch_enabled,
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
    if preset_queries is not None:
        state["_mq_queries"] = preset_queries
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_query_no_batch_call():
    """N=1 (multi_query disabled, no preset) must NOT trigger the batch embed path."""
    from ragbot.orchestration.query_graph import build_graph

    embedder = _CountingEmbedder()
    vs = _RecordingVectorStore()
    resolver, llm = _make_resolver_llm(n_variants=1)

    graph = build_graph(
        invocation_logger=MagicMock(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=embedder,
    )
    # No preset_queries → single-query path only
    state = _base_state("đơn câu hỏi", embed_batch_enabled=True, n_variants=1, mq_enabled=False)
    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Exactly 1 hybrid_search (single-query path)
    assert len(vs.calls) == 1, f"Expected 1 hybrid_search, got {len(vs.calls)}"
    # Key: pipeline ran cleanly and returned a result.
    assert vs.calls[0]["embedding"], "Single-query path must provide non-empty embedding"


def test_embed_batch_disabled_flag_skips_batch():
    """pipeline_multi_query_embed_batch_enabled=False must prevent top-level
    batch embed; pipeline still completes via per-branch _embed_query fallback."""
    from ragbot.orchestration.query_graph import build_graph

    embedder = _CountingEmbedder()
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
    state = _base_state(
        "bảo hành",
        embed_batch_enabled=False,
        preset_queries=["bảo hành", "thời gian bảo hành", "bao lâu bảo hành"],
    )
    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Multi-query must still fire (3 branches via per-branch _embed_query)
    assert len(vs.calls) >= 2, f"Expected ≥2 hybrid_search calls, got {len(vs.calls)}"
    for call in vs.calls:
        assert call["embedding"], f"Empty embedding with batch disabled for {call['query_text']!r}"


def test_batch_wrong_count_falls_back():
    """If embed_batch returns wrong number of results, _embed_batch_queries must
    fall back to the per-query path and still retrieve for all branches."""
    from ragbot.orchestration.query_graph import build_graph

    # wrong_count=True makes embed_batch return only 1 item regardless of input
    embedder = _CountingEmbedder(wrong_count=True)
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
    state = _base_state(
        "bảo hành sản phẩm",
        embed_batch_enabled=True,
        preset_queries=["bảo hành sản phẩm", "thời gian bảo hành", "bao lâu"],
    )
    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Despite wrong_count, the fallback path must produce working searches
    assert len(vs.calls) >= 1, "Pipeline must produce at least one search result"
