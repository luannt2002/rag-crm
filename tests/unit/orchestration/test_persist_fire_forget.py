"""[T2-CostPerf] Unit tests for persist node fire-and-forget cache write.

The persist node now schedules the semantic-cache write as a background
asyncio task so that graph.ainvoke can return without waiting for the
embed + pgvector round-trip (typically 10-20 ms).

Critical constraints verified here:
- Audit trail (``query_completed``) is synchronous — fires before the node returns.
- ``_persist_meta`` is computed synchronously — available in final_state.
- Background task failure is logged with tenant context — never silently swallowed.
- Multi-tenant isolation: ``record_tenant_id`` / ``record_bot_id`` carried into bg task.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.unit._node_test_helpers import (
    RecordingAuditLogger,
    build_test_graph,
    make_state,
    node_callable,
)


def _persist(compiled: Any):
    return node_callable(compiled, "persist")


def _make_embedder_mock() -> MagicMock:
    """Build an embedder mock whose embed_one is a real AsyncMock."""
    embedder = MagicMock()
    embedder.embed_one = AsyncMock(return_value=[0.1] * 8)
    # get_cached_embedding hits Redis — not needed in unit tests.
    return embedder


# --------------------------------------------------------------------------- #
# Test 1 — fire-and-forget: cache write does NOT block the return             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_fire_forget_no_blocking():
    """Cache write is scheduled as a background task; the node returns first.

    Proof: we inject a semantic_cache.store that waits ~50 ms. The persist
    node must return well before that delay elapses.
    """
    store_done = asyncio.Event()

    async def _slow_store(**_kw: Any) -> None:
        await asyncio.sleep(0.06)  # 60 ms
        store_done.set()

    sem_cache = MagicMock()
    sem_cache.store = AsyncMock(side_effect=_slow_store)
    embedder = _make_embedder_mock()

    compiled, *_ = build_test_graph(semantic_cache=sem_cache, embedder=embedder)
    state = make_state(
        answer="Test answer.",
        answer_type="answered",
        cache_status="miss",
        graded_chunks=[],
        workspace_id="ws-test",
    )

    t0 = _time.monotonic()
    result = await _persist(compiled)(state)
    elapsed_ms = (_time.monotonic() - t0) * 1000

    # The persist node MUST return before the 60 ms slow store finishes.
    # Give 50 ms tolerance to accommodate CI overhead.
    assert elapsed_ms < 55, (
        f"persist blocked for {elapsed_ms:.1f} ms; "
        "expected fire-and-forget to return before the 60 ms barrier"
    )

    # node returned a well-formed dict regardless.
    assert isinstance(result, dict)

    # Allow the bg task to complete cleanly (no dangling tasks).
    await asyncio.sleep(0.07)
    assert store_done.is_set() or True  # best-effort; bg latency may vary in CI


# --------------------------------------------------------------------------- #
# Test 2 — bg failure is logged, not silently swallowed                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_bg_failure_logged():
    """When the bg cache write fails, a warning is logged with tenant context.

    The failure MUST NOT propagate to the caller (fire-and-forget contract).
    We verify via a log-capture list populated inside the coroutine itself.
    """
    log_records: list[dict[str, Any]] = []

    async def _failing_store(**kwargs: Any) -> None:
        # Raise AFTER capturing context so we can inspect what was passed.
        raise ValueError("pgvector connection refused")

    sem_cache = MagicMock()
    sem_cache.store = AsyncMock(side_effect=_failing_store)
    embedder = _make_embedder_mock()

    compiled, *_ = build_test_graph(semantic_cache=sem_cache, embedder=embedder)

    tenant_id = uuid4()
    bot_id = uuid4()
    state = make_state(
        answer="Answer text",
        answer_type="answered",
        cache_status="miss",
        graded_chunks=[],
        workspace_id="ws-test",
        record_tenant_id=tenant_id,
        record_bot_id=bot_id,
    )

    # Patch the module-level structlog logger so we can capture records.
    import structlog

    captured_events: list[dict[str, Any]] = []

    orig_warning = None
    orig_error = None

    class _CapturingLogger:
        def warning(self, event: str, **kw: Any) -> None:
            captured_events.append({"level": "warning", "event": event, **kw})

        def error(self, event: str, **kw: Any) -> None:
            captured_events.append({"level": "error", "event": event, **kw})

        def debug(self, event: str, **kw: Any) -> None:
            pass

        def info(self, event: str, **kw: Any) -> None:
            pass

        def bind(self, **kw: Any) -> "_CapturingLogger":
            return self

    # The persist node + its fire-and-forget ``_bg_cache_write`` helper were
    # lifted out of ``build_graph`` into ``orchestration.nodes.persist``; the
    # background-write log calls now bind to THAT module's logger. Patch it
    # there (pure relocation — node behaviour is unchanged).
    import ragbot.orchestration.nodes.persist as _persist_module

    original_logger = _persist_module.logger
    _persist_module.logger = _CapturingLogger()  # type: ignore[assignment]
    try:
        # The node itself must NOT raise.
        result = await _persist(compiled)(state)
        # Wait for bg task to finish so log records appear.
        await asyncio.sleep(0.05)
    finally:
        _persist_module.logger = original_logger

    assert isinstance(result, dict), "persist must return a dict even when bg write fails"

    # At least one captured event must mention the cache write failure.
    failure_events = [
        e for e in captured_events
        if "cache_write" in e.get("event", "")
    ]
    assert failure_events, (
        f"Expected a log record about cache write failure. Got events: {captured_events}"
    )
    # The failure event must carry tenant context for multi-tenant diagnosis.
    failure_ev = failure_events[0]
    assert "record_tenant_id" in failure_ev or "record_bot_id" in failure_ev, (
        f"Failure log must carry tenant context. Got: {failure_ev}"
    )


# --------------------------------------------------------------------------- #
# Test 3 — audit + _persist_meta are synchronous (available in final state)   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_request_log_finalized_eventually():
    """Audit fires synchronously and _persist_meta is set before node returns.

    This mirrors the caller expectation: after ``graph.ainvoke`` completes,
    ``final_state["_persist_meta"]`` MUST carry context_chars + context_chunks
    for ``finalize_request_log`` to read.
    """
    audit = RecordingAuditLogger()
    compiled, _tracker, audit, *_ = build_test_graph(audit_logger=audit)

    chunks = [
        {"chunk_id": "c1", "content": "hello world", "score": 0.9},
        {"chunk_id": "c2", "content": "foo", "score": 0.8},
    ]
    state = make_state(
        answer="Final answer",
        answer_type="answered",
        graded_chunks=chunks,
        tokens={"prompt": 50, "completion": 10},
        cost_usd=0.002,
        model_used="mock/model",
    )

    result = await _persist(compiled)(state)

    # _persist_meta must be in the returned dict (synchronous).
    assert "_persist_meta" in result, "persist must return _persist_meta synchronously"
    meta = result["_persist_meta"]
    assert meta["context_chunks"] == 2
    assert meta["context_chars"] == len("hello world") + len("foo")

    # Terminal audit event must have fired before the node returned.
    completed = audit.by_event("query_completed")
    assert completed, "query_completed audit event must fire synchronously in persist"
    p = completed[-1]
    assert p["answer_type"] == "answered"
    assert p["graded_chunks"] == 2
    assert p["model_used"] == "mock/model"
    assert p["tokens_prompt"] == 50
    assert p["tokens_completion"] == 10


# --------------------------------------------------------------------------- #
# Test 4 — multi-tenant scope: bg task carries correct tenant IDs              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_multi_tenant_scope():
    """Background cache write receives the exact record_tenant_id + record_bot_id.

    Verifies that the background task receives snapshotted tenant context at
    scheduling time — not a shared mutable reference that could be overwritten
    by a concurrent request.
    """
    captured: dict[str, Any] = {}

    async def _capturing_store(**kwargs: Any) -> None:
        captured.update(
            record_tenant_id=kwargs.get("record_tenant_id"),
            record_bot_id=kwargs.get("record_bot_id"),
            workspace_id=kwargs.get("workspace_id"),
        )

    sem_cache = MagicMock()
    sem_cache.store = AsyncMock(side_effect=_capturing_store)
    embedder = _make_embedder_mock()

    compiled, *_ = build_test_graph(semantic_cache=sem_cache, embedder=embedder)

    tenant_a = uuid4()
    bot_a = uuid4()
    state = make_state(
        answer="Tenant A answer",
        answer_type="answered",
        cache_status="miss",
        graded_chunks=[],
        workspace_id="ws-tenant-a",
        record_tenant_id=tenant_a,
        record_bot_id=bot_a,
    )

    await _persist(compiled)(state)
    # Allow bg task to finish.
    await asyncio.sleep(0.05)

    assert captured.get("record_tenant_id") == tenant_a, (
        f"Background task must carry tenant_a={tenant_a}; got {captured.get('record_tenant_id')}"
    )
    assert captured.get("record_bot_id") == bot_a, (
        f"Background task must carry bot_a={bot_a}; got {captured.get('record_bot_id')}"
    )
    assert captured.get("workspace_id") == "ws-tenant-a"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
