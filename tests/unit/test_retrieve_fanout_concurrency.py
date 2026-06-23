"""J-3: retrieve multi-query fan-out must be semaphore-bounded.

Each fan-out branch (`_run_hybrid_for_query`) opens its own DB session from a
fixed pool. An unbounded ``asyncio.gather`` over N variants / sub-queries spikes
N concurrent sessions, which exhausts the pool under concurrent turns
(Async Performance Rule 6). The fan-out must be bounded by
``DEFAULT_RETRIEVE_FANOUT_CONCURRENCY`` (per-bot override via pipeline_config),
mirroring the bounded CRAG grader fan-out.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ragbot.shared.constants import (
    DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY,
    DEFAULT_RETRIEVE_FANOUT_CONCURRENCY,
)
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _ConcurrencyTrackingVectorStore:
    """Records peak concurrent in-flight hybrid_search calls."""

    def __init__(self):
        self.calls: list[str] = []
        self._in_flight = 0
        self.peak_in_flight = 0

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id,
        top_k: int,
        **kw,
    ) -> list[dict]:
        self._in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        # Yield so all started branches overlap before any completes — without
        # the await, branches would run to completion one at a time.
        await asyncio.sleep(0.02)
        self.calls.append(query_text)
        self._in_flight -= 1
        cid = f"chunk-{len(self.calls)}"
        return [
            {
                "chunk_id": cid,
                "text": f"hit {query_text[:20]}",
                "content": f"hit {query_text[:20]}",
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
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw):
        return {
            "text": "Answer text.",
            "prompt_tokens": 1, "completion_tokens": 1,
            "cost_usd": 0.0, "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


def _base_state(query: str, sub_queries: list[str]):
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
        "sub_queries": sub_queries,
        "pipeline_config": {
            "multi_query_enabled": False,
            "decompose_enabled": False,
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


def test_fanout_peak_concurrency_bounded_by_constant():
    """A 10-branch fan-out must not exceed DEFAULT_RETRIEVE_FANOUT_CONCURRENCY in-flight."""
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _resolver_llm()
    vs = _ConcurrencyTrackingVectorStore()
    graph = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    sub_queries = [f"sub-query number {i}" for i in range(10)]
    state = _base_state("ten-branch fan-out", sub_queries)

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    assert len(vs.calls) == 10, f"all 10 branches must run, got {len(vs.calls)}"
    assert vs.peak_in_flight <= DEFAULT_RETRIEVE_FANOUT_CONCURRENCY, (
        f"fan-out peak {vs.peak_in_flight} exceeded cap "
        f"{DEFAULT_RETRIEVE_FANOUT_CONCURRENCY} — gather is unbounded"
    )
