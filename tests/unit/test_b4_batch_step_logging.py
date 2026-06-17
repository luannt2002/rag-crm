"""Phase-B B4 — Batch ``request_steps`` INSERT + async flush.

Unit tests for ``StepTracker`` buffering + ``RequestLogRepository.add_steps_batch``
without touching the database. The DB layer is exercised separately by the
existing repo integration tests (out of B4 scope; B4 = pytest unit only per
HANDOFF).

Behaviour matrix:
  * ``batch_enabled=False`` (default) — per-step write via ``add_step`` (legacy).
  * ``batch_enabled=True`` — every ``step()`` exit only buffers; ``flush()``
    issues exactly ONE ``add_steps_batch`` call carrying all rows in order.
  * Crash mid-pipeline does not leak rows: the wrapping context manager
    propagates the exception, but rows already buffered survive for a later
    flush AND no per-step writes have hit ``add_step``.
  * ``flush()`` is idempotent + best-effort (a repo failure is logged, not
    re-raised, so the surrounding chat_worker cleanup keeps running).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from ragbot.application.services.step_tracker import StepTracker

_TENANT = UUID("00000000-0000-0000-0000-000000000099")
_REQ = UUID("11111111-1111-1111-1111-1111111111aa")


class _RecordingRepo:
    """Captures every ``add_step`` + ``add_steps_batch`` invocation."""

    def __init__(self) -> None:
        self.single_rows: list[dict[str, Any]] = []
        self.batch_calls: list[list[dict[str, Any]]] = []

    async def add_step(self, **kwargs: Any) -> None:
        self.single_rows.append(kwargs)

    async def add_steps_batch(
        self,
        *,
        request_id: UUID,  # noqa: ARG002 — captured implicitly; assertions read from batch_calls only.
        record_tenant_id: UUID,  # noqa: ARG002
        steps: list[dict[str, Any]],
    ) -> int:
        # Defensive copy — caller mutates self._buffer otherwise.
        self.batch_calls.append([dict(s) for s in steps])
        return len(steps)


class _FailingBatchRepo(_RecordingRepo):
    """Batch path raises — flush MUST swallow + return 0."""

    async def add_steps_batch(self, **_kwargs: Any) -> int:  # type: ignore[override]
        raise RuntimeError("simulated DB failure")


# ---------------------------------------------------------------------------
# 1. Default behaviour preserved when batch_enabled is False.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_per_step_write_when_batch_disabled() -> None:
    """Off-mode tracker writes one row per ``step()`` exit via ``add_step``.

    Confirms a missed flush call does NOT regress the legacy code path —
    every step still lands in ``request_steps`` immediately as today.
    """
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        # batch_enabled defaults to False
    )
    assert tracker.batch_enabled is False

    async with tracker.step("retrieve"):
        pass
    async with tracker.step("rerank"):
        pass

    # 2 per-step writes, 0 batched.
    assert len(repo.single_rows) == 2
    assert repo.batch_calls == []
    assert [r["step_name"] for r in repo.single_rows] == ["retrieve", "rerank"]
    # buffer never populated when off.
    assert tracker.buffer_size == 0


# ---------------------------------------------------------------------------
# 2. Batch mode buffers rows + single batched write on flush.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_batch_mode_buffers_then_flushes_in_one_call() -> None:
    """Buffered mode collapses N ``step()`` exits into a single
    ``add_steps_batch`` round-trip (the B4 saving)."""
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        batch_enabled=True,
    )
    assert tracker.batch_enabled is True

    # Simulate 5 representative pipeline stages.
    names = ["guard_input", "retrieve", "rerank", "grade", "generate"]
    for name in names:
        async with tracker.step(name) as ctx:
            ctx.add_tokens(prompt=2, completion=3, cost_usd=0.001)

    # Nothing committed yet — observable buffer size matches step count.
    assert tracker.buffer_size == len(names)
    assert repo.single_rows == []
    assert repo.batch_calls == []

    written = await tracker.flush()

    assert written == len(names)
    assert repo.single_rows == [], "per-step path must remain dormant in batch mode"
    assert len(repo.batch_calls) == 1
    batch = repo.batch_calls[0]
    # Order preserved == step_order monotonically increasing.
    assert [r["step_name"] for r in batch] == names
    assert [r["step_order"] for r in batch] == [1, 2, 3, 4, 5]
    # Token / cost aggregation per row survives buffering.
    for row in batch:
        assert row["input_tokens"] == 2
        assert row["output_tokens"] == 3
        assert row["cost_usd"] == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# 3. Pipeline crash mid-flight: in-flight exception propagates BUT the rows
#    that already executed remain buffered for a subsequent flush call.
#    No data loss as long as the chat_worker flushes once in its finally.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_crash_midway_preserves_buffered_rows() -> None:
    """Tracker's ``step`` re-raises the inner exception so the caller still
    sees the original failure; the failing step itself is buffered with
    ``status=failed`` so post-mortem analytics can read the trail."""
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        batch_enabled=True,
    )

    async with tracker.step("guard_input"):
        pass

    with pytest.raises(RuntimeError, match="retrieve_blew_up"):
        async with tracker.step("retrieve"):
            raise RuntimeError("retrieve_blew_up")

    # 1 success row + 1 failed row buffered; the failed row carries the error.
    assert tracker.buffer_size == 2
    flushed = await tracker.flush()
    assert flushed == 2
    batch = repo.batch_calls[0]
    assert [r["step_name"] for r in batch] == ["guard_input", "retrieve"]
    assert batch[0]["status"] == "success"
    assert batch[1]["status"] == "failed"
    assert batch[1]["error"] == "retrieve_blew_up"


# ---------------------------------------------------------------------------
# 4. flush() is idempotent; second call after a successful flush is a no-op.
#    Caller (chat_worker) can safely call flush() inside an outer try/except
#    without double-writing.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flush_is_idempotent() -> None:
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        batch_enabled=True,
    )

    async with tracker.step("retrieve"):
        pass
    async with tracker.step("rerank"):
        pass

    first = await tracker.flush()
    second = await tracker.flush()
    third = await tracker.flush()

    assert first == 2
    assert second == 0
    assert third == 0
    assert len(repo.batch_calls) == 1
    assert tracker.buffer_size == 0


# ---------------------------------------------------------------------------
# 5. flush() on empty buffer (no step() calls) is a no-op — returns 0,
#    does not call the repo (would emit a useless empty INSERT otherwise).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flush_with_empty_buffer_is_noop() -> None:
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        batch_enabled=True,
    )

    written = await tracker.flush()
    assert written == 0
    assert repo.batch_calls == []
    assert repo.single_rows == []


# ---------------------------------------------------------------------------
# 6. flush() must swallow repo failures — the user-facing answer has already
#    been emitted by chat_worker, observability loss is preferable to crashing
#    post-response cleanup (CLAUDE.md graceful-degradation rule).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flush_swallows_repo_failure() -> None:
    repo = _FailingBatchRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        batch_enabled=True,
    )
    async with tracker.step("retrieve"):
        pass

    # Buffer non-empty pre-flush.
    assert tracker.buffer_size == 1
    written = await tracker.flush()

    # Repo blew up — flush reports zero rows written but DOES NOT raise.
    assert written == 0


# ---------------------------------------------------------------------------
# 7. step_kind metadata still injected in buffered rows (Phase D ingest split
#    relies on this; B4 must not regress that invariant).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step_kind_metadata_propagates_through_buffer() -> None:
    repo = _RecordingRepo()
    tracker = StepTracker(
        request_id=_REQ,
        record_tenant_id=_TENANT,
        repo=repo,
        kind="query",
        batch_enabled=True,
    )

    async with tracker.step("retrieve"):
        pass

    await tracker.flush()
    row = repo.batch_calls[0][0]
    assert row["metadata"]["step_kind"] == "query"
