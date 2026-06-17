"""[T1-Smartness][T2-CostPerf] Pin tests for stats-vs-vector race in retrieve node.

When ``stats_index_race_enabled=true`` in pipeline_config, the retrieve node
fires stats-index SQL and a single-shot vector search concurrently and returns
the result of whichever path completes first with a non-empty result.

Tests:
  1. stats_wins  — stats returns first → vector task cancelled
  2. vector_wins — stats slower / empty → vector returns first
  3. both_empty  — neither path returns data → fallback path taken (no crash)
  4. timeout_safety — 2 s timeout fires → fallback, no hanging
  5. cancel_cleanup — losing task is always cancelled when winner found
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from ragbot.orchestration import query_graph as qg


# ---------------------------------------------------------------------------
# Shared test doubles
# ---------------------------------------------------------------------------


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


class _RecordingStepCtx:
    def __init__(self) -> None:
        self.metadata: dict = {}

    def set_metadata(self, **kwargs) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kw) -> None:
        pass

    def record_llm(self, **_kw) -> None:
        pass

    def record(self, **_kw) -> None:
        pass


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.last_ctx: _RecordingStepCtx | None = None

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx()
        self.last_ctx = ctx
        yield ctx


def _make_llm_and_resolver():
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock()
    cfg.provider.name = "mock-provider"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value={
            "text": "answer",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost_usd": 0.0,
            "finish_reason": "stop",
        }
    )
    return resolver, llm


def _base_state(tracker: _RecordingStepTracker) -> dict:
    return {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "workspace_id": "default",
        # Query contains Vietnamese range signal ("dưới 2tr") so
        # _parse_range_query returns a confident range filter.
        "query": "dưới 2tr có bao nhiêu dịch vụ",
        "rewritten_query": None,
        "sub_queries": [],
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "intent": "aggregation",
        "intent_confidence": 0.9,
        # Race enabled via per-bot pipeline_config.
        "pipeline_config": {"stats_index_race_enabled": True},
        "step_tracker": tracker,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }


def _make_fake_vector_store() -> MagicMock:
    """A vector store whose hybrid_search accepts the old-style signature."""
    vs = MagicMock()
    vs.hybrid_search = AsyncMock(return_value=[])
    vs.search = AsyncMock(return_value=[])
    # Old-style port: signature has query_text in parameters
    import inspect  # local import keeps test module import-light

    async def _hs(query_text, query_embedding, record_bot_id, top_k, **_kw):  # noqa: ANN001
        return []

    vs.hybrid_search = AsyncMock(side_effect=_hs)
    # Expose a real signature so inspect.signature works in _race_vector
    vs.hybrid_search.__signature__ = inspect.signature(_hs)
    return vs


class _FakeEmbedder:
    """Embedder stub that returns a deterministic 8-dim vector."""

    async def embed_one(self, text: str, *, spec=None, record_tenant_id=None) -> list[float]:
        return [0.1] * 8

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 8 for _ in texts]


def _build_compiled(
    *,
    stats_index_repo=None,
    doc_repo=None,
    vector_store=None,
    embedder=None,
):
    resolver, llm = _make_llm_and_resolver()
    return qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        llm=llm,
        model_resolver=resolver,
        vector_store=vector_store,
        stats_index_repo=stats_index_repo,
        doc_repo=doc_repo,
        embedder=embedder,
    )


def _invoke_retrieve(compiled, state: dict) -> dict:
    pregel_node = compiled.nodes["retrieve"]
    runnable = pregel_node.bound
    return asyncio.run(runnable.ainvoke(state))


# ---------------------------------------------------------------------------
# 1. Stats wins: stats returns non-empty first → vector task cancelled
# ---------------------------------------------------------------------------


def test_stats_vector_race_stats_wins() -> None:
    """When stats_index_repo returns non-empty entities before vector, the
    retrieve node returns the stats result with retrieve_mode='stats_race_winner'
    and the stats_entities key is populated."""
    fake_entities = [
        {"entity_name": "Dịch vụ A", "price_primary": 1_500_000, "record_chunk_id": None},
    ]
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)

    vs = _make_fake_vector_store()

    tracker = _RecordingStepTracker()
    state = _base_state(tracker)
    compiled = _build_compiled(stats_index_repo=stats_repo, vector_store=vs)

    result = _invoke_retrieve(compiled, state)

    # Stats path was called.
    stats_repo.query_by_price_range.assert_called_once()
    # Winner metadata set correctly.
    assert result.get("retrieve_mode") == "stats_race_winner"
    assert result.get("stats_entities") == fake_entities
    # Step context source = winner arm.
    assert tracker.last_ctx is not None
    assert tracker.last_ctx.metadata.get("source") == "stats_race_winner"
    assert tracker.last_ctx.metadata.get("entity_count") == 1


# ---------------------------------------------------------------------------
# 2. Vector wins: stats slow/empty → vector retrieves first
# ---------------------------------------------------------------------------


def test_stats_vector_race_vector_wins() -> None:
    """When stats_index_repo returns empty but vector returns chunks, the
    retrieve node returns vector chunks with retrieve_mode='vector_race_winner'."""
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=[])

    fake_chunks = [{"content": "some text", "score": 0.8, "chunk_id": str(uuid4())}]

    vs = MagicMock()

    async def _hs(query_text, query_embedding, record_bot_id, top_k, **_kw):
        return fake_chunks

    vs.hybrid_search = AsyncMock(side_effect=_hs)
    vs.hybrid_search.__signature__ = inspect.signature(_hs)
    vs.search = AsyncMock(return_value=[])

    # Provide a working embedder so _embed_query returns a non-empty vector
    # and _race_vector can proceed to call hybrid_search.
    tracker = _RecordingStepTracker()
    state = _base_state(tracker)
    compiled = _build_compiled(
        stats_index_repo=stats_repo,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )
    result = _invoke_retrieve(compiled, state)

    assert result.get("retrieve_mode") == "vector_race_winner"
    assert result.get("retrieved_chunks") == fake_chunks
    assert tracker.last_ctx is not None
    assert tracker.last_ctx.metadata.get("source") == "vector_race_winner"


# ---------------------------------------------------------------------------
# 3. Both empty: fallback path taken
# ---------------------------------------------------------------------------


def test_stats_vector_race_both_empty() -> None:
    """When both stats and vector return empty/None, the retrieve node falls
    through to the full sequential vector path (no crash, no spurious result)."""
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=[])

    vs = _make_fake_vector_store()

    tracker = _RecordingStepTracker()
    state = _base_state(tracker)
    compiled = _build_compiled(stats_index_repo=stats_repo, vector_store=vs)

    # Should not raise and should not return a stats_race_winner result.
    result = _invoke_retrieve(compiled, state)

    assert result.get("retrieve_mode") != "stats_race_winner"
    assert result.get("retrieve_mode") != "vector_race_winner"
    # stats_entities must NOT be present (would be misleading on fallback).
    assert result.get("stats_entities") is None


# ---------------------------------------------------------------------------
# 4. Timeout safety: 2 s timeout fires → fallback, no hanging
# ---------------------------------------------------------------------------


def test_stats_vector_race_timeout_safety() -> None:
    """When both race arms hang past the configured timeout, the retrieve node
    cancels both tasks and falls through to the sequential vector path without
    raising or hanging."""

    async def _slow_stats(**_kw):
        await asyncio.sleep(10)  # exceeds any reasonable race timeout
        return []

    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(side_effect=_slow_stats)

    async def _slow_hybrid(query_text, query_embedding, record_bot_id, top_k, **_kw):
        await asyncio.sleep(10)
        return []

    vs = MagicMock()
    vs.hybrid_search = AsyncMock(side_effect=_slow_hybrid)
    vs.hybrid_search.__signature__ = inspect.signature(_slow_hybrid)
    vs.search = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker)
    # Set a very short timeout so the test runs fast.
    state["pipeline_config"] = {
        "stats_index_race_enabled": True,
        "stats_race_timeout_s": 0.05,
    }
    compiled = _build_compiled(
        stats_index_repo=stats_repo,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )

    # Must not hang; the fallback path should not raise either.
    result = _invoke_retrieve(compiled, state)

    assert result.get("retrieve_mode") not in ("stats_race_winner", "vector_race_winner")
    assert tracker.last_ctx is not None


# ---------------------------------------------------------------------------
# 5. Cancel cleanup: loser task is always cancelled when winner found
# ---------------------------------------------------------------------------


def test_stats_vector_race_cancel_cleanup() -> None:
    """The losing vector arm is not allowed to produce results after the stats
    arm wins.

    We verify cancel semantics indirectly: the vector arm's hybrid_search mock
    is given a slow coroutine (10 s sleep).  When stats wins quickly, the race
    code cancels the vector task.  If cancellation does NOT happen, the
    asyncio.run() loop in _invoke_retrieve would block for 10 s and pytest
    would timeout.  The test passes fast (< 2 s) only when the task is
    cancelled promptly.

    Additionally we assert the correct winner metadata so the cancel path is
    exercised in a black-box style.
    """
    fake_entities = [
        {"entity_name": "Svc X", "price_primary": 900_000, "record_chunk_id": None},
    ]
    stats_repo = MagicMock()
    stats_repo.query_by_price_range = AsyncMock(return_value=fake_entities)

    vector_call_count = [0]

    async def _slow_or_never_hs(query_text, query_embedding, record_bot_id, top_k, **_kw):
        """Simulate an infinite vector search; if cancelled, CancelledError propagates."""
        vector_call_count[0] += 1
        await asyncio.sleep(30)  # longer than any test timeout — must be cancelled
        return []

    vs = MagicMock()
    vs.hybrid_search = AsyncMock(side_effect=_slow_or_never_hs)
    vs.hybrid_search.__signature__ = inspect.signature(_slow_or_never_hs)
    vs.search = AsyncMock(return_value=[])

    tracker = _RecordingStepTracker()
    state = _base_state(tracker)
    # Use a short race timeout so we don't wait 30 s; the stats arm
    # returns immediately (no sleep), so stats wins before the timeout.
    state["pipeline_config"] = {
        "stats_index_race_enabled": True,
        "stats_race_timeout_s": 5.0,
    }
    compiled = _build_compiled(
        stats_index_repo=stats_repo,
        vector_store=vs,
        embedder=_FakeEmbedder(),
    )

    import time  # noqa: PLC0415
    t0 = time.monotonic()
    result = _invoke_retrieve(compiled, state)
    elapsed = time.monotonic() - t0

    # The retrieve call must finish quickly (well under 5 s) — the race
    # uses asyncio.wait(FIRST_COMPLETED) and cancels the slow vector task.
    assert elapsed < 5.0, (
        f"retrieve took {elapsed:.1f}s — vector task was NOT cancelled after stats won"
    )
    # Stats arm produced the correct winner result.
    assert result.get("retrieve_mode") == "stats_race_winner"
    assert result.get("stats_entities") == fake_entities
    assert tracker.last_ctx is not None
    assert tracker.last_ctx.metadata.get("source") == "stats_race_winner"
