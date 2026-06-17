"""Tests for R4 root-cause retrieve fallback (multi-query empty → original Q).

Background: R4 audit found 11 R2-PASS turns regressed to R4-REFUSE because
multi-query LLM stochasticity dropped key terms across all paraphrases →
RRF merge yielded 0 candidates → bot refused. This fallback retries
hybrid_search ONCE with the original verbatim user query and a smaller
top_k (precision focus) to rescue borderline turns.

Wires the real ``build_graph(...)`` against a fake vector_store that
simulates two regimes:
  * primary path returns 0 chunks
  * a second call (the rescue) returns a single chunk

Five gates covered:
  1. multi_query empty → fallback fires with ORIGINAL Q + smaller top_k
  2. multi_query returns chunks → fallback NOT fired (single call to store)
  3. fallback also returns 0 → final empty (legit, no rescue)
  4. retrieve_fallback_enabled=False → fallback skipped even on empty
  5. state["retrieve_mode"] == "fallback_original" when rescue succeeds
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
# Shared fakes (mirrored from test_multi_query_pipeline_integration)          #
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


class _PrimaryEmptyThenRescueStore:
    """Returns [] for the first N calls (multi-query branches), then a hit
    on the rescue retry. Records every hybrid_search call for assertions.
    """

    def __init__(self, *, primary_branches: int, rescue_chunks: list[dict] | None):
        self.calls: list[dict] = []
        self._primary_branches = primary_branches
        self._rescue_chunks = rescue_chunks if rescue_chunks is not None else []

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
        # First N calls = multi-query fanout branches → empty.
        if len(self.calls) <= self._primary_branches:
            return []
        # Subsequent call(s) = rescue retry → return configured chunks.
        return list(self._rescue_chunks)

    async def search(self, **kw):  # pragma: no cover — not used here
        return []


class _AlwaysReturnsStore:
    """Returns the same chunk on every hybrid_search call — no fallback needed."""

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
                "text": f"hit for {query_text[:30]}",
                "content": f"hit for {query_text[:30]}",
                "score": 0.5,
            },
        ]

    async def search(self, **kw):  # pragma: no cover
        return []


class _FakeEmbedder:
    async def embed(self, texts, **_kw):
        if isinstance(texts, list):
            return [[0.1] * 8 for _ in texts]
        return [[0.1] * 8]

    async def embed_batch(self, texts, **_kw):
        return [[0.1] * 8 for _ in texts]


def _resolver_llm(*, paraphrase_text: str = '["alt 1", "alt 2"]'):
    """Resolver + LLM mock; multi_query purpose returns paraphrase_text."""
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
        if purpose == "multi_query":
            return {
                "text": paraphrase_text,
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        if "phân loại intent" in joined:
            user_q = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"),
                "",
            )
            return {
                "text": '{"query": "' + user_q + '", "intent": "factoid"}',
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            return {
                "text": "Chunk 1: relevant",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        return {
            "text": "Answer text.",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost_usd": 0.0,
            "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(
    query: str,
    *,
    multi_query_enabled: bool = True,
    n_variants: int = 3,
    fallback_enabled: bool = True,
    fallback_top_k: int = 5,
):
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
            "multi_query_enabled": multi_query_enabled,
            "multi_query_n_variants": n_variants,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            "merge_condense_router": True,
            "decompose_enabled": False,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": "mock/model",
            "embedding_dimension": 8,
            "top_k": 10,
            "reranker_enabled": False,
            "rag_rrf_k": 60,
            "retrieve_fallback_enabled": fallback_enabled,
            "retrieve_fallback_top_k": fallback_top_k,
            # Disable CRAG retry loops so retrieve fires exactly once per
            # graph invocation; otherwise grade/reflect bounces back to
            # retrieve and doubles every counted call.
            "max_grade_retries": 0,
            "max_reflect_retries": 0,
        },
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}


def _build_graph(vector_store):
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm(paraphrase_text='["bao lâu", "thời hạn"]')
    return build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vector_store,
        embedder=_FakeEmbedder(),
    )


# --------------------------------------------------------------------------- #
# Gate 1: multi-query empty → fallback fires with ORIGINAL Q + smaller top_k  #
# --------------------------------------------------------------------------- #


def test_multi_query_empty_triggers_fallback_with_original_query():
    rescue_chunk = {
        "chunk_id": "rescue-1",
        "text": "rescue hit",
        "content": "rescue hit",
        "score": 0.7,
    }
    vs = _PrimaryEmptyThenRescueStore(primary_branches=3, rescue_chunks=[rescue_chunk])
    graph = _build_graph(vs)
    state = _base_state("bảo hành xe điện", fallback_top_k=5)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # 3 multi-query branches + 1 rescue retry = 4 hybrid_search calls.
    assert len(vs.calls) == 4, (
        f"expected 3 fanout + 1 fallback, got {len(vs.calls)}: {vs.calls}"
    )
    # Last call is the rescue: must use the ORIGINAL verbatim query and the
    # smaller fallback top_k (5), NOT the global top_k (10).
    rescue_call = vs.calls[-1]
    assert "bảo hành xe điện" in rescue_call["query_text"], rescue_call
    assert rescue_call["top_k"] == 5, rescue_call


# --------------------------------------------------------------------------- #
# Gate 2: multi-query returns chunks → fallback NOT fired                     #
# --------------------------------------------------------------------------- #


def test_multi_query_with_chunks_does_not_trigger_fallback():
    vs = _AlwaysReturnsStore()
    graph = _build_graph(vs)
    state = _base_state("bảo hành xe điện")

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Exactly 3 multi-query branches; no fallback retry.
    assert len(vs.calls) == 3, (
        f"happy-path multi-query must NOT trigger fallback; got {len(vs.calls)} calls"
    )


# --------------------------------------------------------------------------- #
# Gate 3: fallback also returns 0 → final state has no chunks                 #
# --------------------------------------------------------------------------- #


def test_fallback_empty_keeps_pipeline_chunkless():
    vs = _PrimaryEmptyThenRescueStore(primary_branches=3, rescue_chunks=[])
    graph = _build_graph(vs)
    state = _base_state("câu hỏi không có trong tài liệu")

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Fallback was attempted (call 4) but returned [] — legit no-context.
    assert len(vs.calls) == 4, (
        f"fallback must still be attempted; got {len(vs.calls)} calls"
    )
    assert not final.get("retrieved_chunks"), (
        "fallback empty → state.retrieved_chunks must remain empty"
    )
    # Marker must NOT be set when rescue produced nothing.
    assert final.get("retrieve_mode") != "fallback_original", (
        "marker only set on successful rescue, not on empty rescue"
    )


# --------------------------------------------------------------------------- #
# Gate 4: retrieve_fallback_enabled=False → fallback skipped                  #
# --------------------------------------------------------------------------- #


def test_fallback_toggle_off_skips_rescue():
    vs = _PrimaryEmptyThenRescueStore(
        primary_branches=3,
        rescue_chunks=[{"chunk_id": "x", "text": "x", "content": "x", "score": 0.5}],
    )
    graph = _build_graph(vs)
    state = _base_state("bảo hành", fallback_enabled=False)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # Only 3 fanout calls; the 4th rescue must be skipped when toggle is off.
    assert len(vs.calls) == 3, (
        f"fallback toggle OFF must skip the 4th call; got {len(vs.calls)}"
    )


# --------------------------------------------------------------------------- #
# Gate 5: state["retrieve_mode"] == "fallback_original" on successful rescue   #
# --------------------------------------------------------------------------- #


def test_fallback_success_sets_retrieve_mode_marker():
    rescue_chunk = {
        "chunk_id": "rescue-2",
        "text": "rescue hit 2",
        "content": "rescue hit 2",
        "score": 0.8,
    }
    vs = _PrimaryEmptyThenRescueStore(primary_branches=3, rescue_chunks=[rescue_chunk])
    graph = _build_graph(vs)
    state = _base_state("bảo hành xe điện")

    final = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert final.get("retrieve_mode") == "fallback_original", (
        f"successful rescue must mark retrieve_mode; got {final.get('retrieve_mode')}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
