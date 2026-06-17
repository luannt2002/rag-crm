"""Finding #13 perf invariant — ``PipelineAuditLogger.log`` MUST NOT call
``open()`` directly on the event-loop thread. The write is offloaded to
``asyncio.to_thread`` so the file IO can't stall co-running coroutines.

Symptom before the fix: under load, a single audit append could block
the event loop for tens of milliseconds (especially on slow disks /
network filesystems) — every concurrent chat turn paid the latency.

Test strategy:
1. Patch ``builtins.open`` to capture which thread executes it. Assert
   it runs on a worker thread, NOT the main loop thread.
2. Patch ``asyncio.to_thread`` to count calls; assert exactly one
   ``to_thread`` per ``log()`` call.
3. Validate the written file still ends up correct — perf fix MUST NOT
   change observable output.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ragbot.infrastructure.observability import pipeline_audit_logger as mod
from ragbot.infrastructure.observability.pipeline_audit_logger import (
    PipelineAuditLogger,
)


_ENV = "RAGBOT_PIPELINE_AUDIT_ENABLED"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


@pytest.fixture(autouse=True)
def _reset_locks(monkeypatch):
    """Reset module locks between tests so each loop gets fresh asyncio.Lock."""
    monkeypatch.delenv(_ENV, raising=False)
    PipelineAuditLogger._locks.clear()


def test_log_offloads_open_to_worker_thread(tmp_path: Path, monkeypatch) -> None:
    """The file ``open(path, 'a')`` call must execute on a NON-main thread.

    Captures the threading identity inside the wrapped sync helper and
    asserts it differs from the event-loop thread. This is the
    behavioural cap of the perf fix.
    """
    monkeypatch.setenv(_ENV, "true")

    loop_thread_id: dict[str, int] = {}
    write_thread_ids: list[int] = []

    real_write_line_sync = mod._write_line_sync

    def _spy_write(path: Path, line: str) -> None:
        write_thread_ids.append(threading.get_ident())
        real_write_line_sync(path, line)

    monkeypatch.setattr(mod, "_write_line_sync", _spy_write)

    log = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> None:
        loop_thread_id["main"] = threading.get_ident()
        await log.log("bot-async", "ingest", "ingest_started", {"raw_len": 5})

    asyncio.run(_run())

    assert write_thread_ids, "spy never called — write path bypassed"
    assert "main" in loop_thread_id
    for tid in write_thread_ids:
        assert tid != loop_thread_id["main"], (
            "file write executed on the event-loop thread — "
            "Finding #13 perf invariant broken"
        )


def test_log_uses_asyncio_to_thread_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """Counts ``asyncio.to_thread`` invocations from inside ``log()``.

    The contract: one offload per ``log()`` call when enabled. Zero
    when disabled.
    """
    monkeypatch.setenv(_ENV, "true")

    counter = {"n": 0}
    real_to_thread = asyncio.to_thread

    async def _counting_to_thread(func, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        counter["n"] += 1
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(mod.asyncio, "to_thread", _counting_to_thread)

    log = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> None:
        await log.log("bot-x", "query", "q1", {"i": 1})
        await log.log("bot-x", "query", "q2", {"i": 2})

    asyncio.run(_run())
    assert counter["n"] == 2, (
        f"expected 2 to_thread offloads (one per log); got {counter['n']}"
    )


def test_log_disabled_skips_to_thread(tmp_path: Path, monkeypatch) -> None:
    """Disabled logger must NOT offload to a thread at all — the early
    return path runs before any IO is scheduled.
    """
    # Env explicitly off.
    monkeypatch.setenv(_ENV, "false")

    counter = {"n": 0}
    real_to_thread = asyncio.to_thread

    async def _counting_to_thread(func, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        counter["n"] += 1
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(mod.asyncio, "to_thread", _counting_to_thread)

    log = PipelineAuditLogger(output_dir=str(tmp_path))
    asyncio.run(log.log("bot-off", "ingest", "x", {"i": 1}))
    assert counter["n"] == 0


def test_log_output_unchanged_after_async_offload(tmp_path: Path, monkeypatch) -> None:
    """Behavioural invariant: the JSONL line written through ``to_thread``
    is byte-equivalent to the previous sync-write contract."""
    monkeypatch.setenv(_ENV, "true")
    log = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> None:
        await log.log("bot-z", "ingest", "ingest_started", {"raw_len": 99})

    asyncio.run(_run())
    out_path = tmp_path / f"pipeline_audit_bot-z_{_today()}.jsonl"
    text = out_path.read_text(encoding="utf-8")
    assert text.endswith("\n"), "missing trailing newline"
    record = json.loads(text.splitlines()[0])
    assert record["stage"] == "ingest"
    assert record["event"] == "ingest_started"
    assert record["data"]["raw_len"] == 99


def test_high_concurrency_writes_via_offload(tmp_path: Path, monkeypatch) -> None:
    """50 concurrent ``log()`` coroutines still produce 50 well-formed
    lines — the offload + per-path lock combo keeps writes serialised
    inside the same loop while not blocking the loop itself.
    """
    monkeypatch.setenv(_ENV, "true")
    log = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> None:
        coros = [
            log.log("bot-c", "query", "ev", {"i": i})
            for i in range(50)
        ]
        await asyncio.gather(*coros)

    asyncio.run(_run())

    expected = tmp_path / f"pipeline_audit_bot-c_{_today()}.jsonl"
    lines = expected.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50, f"expected 50 lines; got {len(lines)}"
    indices = sorted(json.loads(line)["data"]["i"] for line in lines)
    assert indices == list(range(50))
