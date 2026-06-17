"""PipelineAuditLogger per-path lock pool must not leak.

Pre-fix: class-level ``_locks: dict[str, asyncio.Lock]`` accumulated one
entry per unique output path (one per bot per day, plus rotation
slots). Over weeks a long-running worker would grow the dict to
thousands of entries.

Fix: WeakValueDictionary, mirror of the semantic_cache pool.
"""

from __future__ import annotations

import asyncio
import gc
import weakref
from pathlib import Path

import pytest

from ragbot.infrastructure.observability.pipeline_audit_logger import (
    PipelineAuditLogger,
)


def test_class_lock_pool_is_weakvaluedictionary() -> None:
    """Class-level pool MUST be the weak-ref dict, not a plain dict."""
    assert isinstance(
        PipelineAuditLogger._locks, weakref.WeakValueDictionary
    ), (
        "PipelineAuditLogger._locks MUST be WeakValueDictionary; "
        f"got {type(PipelineAuditLogger._locks).__name__}"
    )


def test_lock_entry_garbage_collected_after_log(tmp_path: Path, monkeypatch) -> None:
    """After ``log()`` completes, the per-path lock MUST be reapable."""

    # Force-enable the logger via env (default is OFF).
    monkeypatch.setenv("RAGBOT_PIPELINE_AUDIT_ENABLED", "true")
    logger = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> int:
        await logger.log("bot-a", "stage-1", "evt", {"x": 1})
        return len(PipelineAuditLogger._locks)

    # Snapshot count before — there may be unrelated entries from other
    # tests sharing the class-level pool.
    gc.collect()
    pre = len(PipelineAuditLogger._locks)
    after_log = asyncio.run(_run())
    gc.collect()
    post = len(PipelineAuditLogger._locks)

    # During ``async with lock`` the entry must exist; once the awaitable
    # returns and we collect, the only ref is the weak one and the entry
    # must be reapable.
    assert post <= pre + 1, (
        f"per-path lock leaked across log() call: pre={pre}, "
        f"after-call={after_log}, post-gc={post}"
    )


def test_concurrent_log_to_same_path_serialises(tmp_path: Path, monkeypatch) -> None:
    """Concurrent ``log()`` to the same path MUST contend on one lock.

    Regression: a premature weak-ref reap would let two coroutines hold
    two different lock objects for the same file, defeating the
    serialisation invariant.
    """

    monkeypatch.setenv("RAGBOT_PIPELINE_AUDIT_ENABLED", "true")
    logger = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> int:
        # Fire 20 concurrent writes; each grabs the same lock object.
        tasks = [logger.log("bot-z", "stage", f"evt{i}", {}) for i in range(20)]
        await asyncio.gather(*tasks)
        # All 20 lines must be present in the file.
        files = list(tmp_path.glob("pipeline_audit_bot-z_*.jsonl"))
        assert len(files) == 1, f"expected 1 file, got {files}"
        return sum(1 for _ in files[0].read_text().splitlines())

    line_count = asyncio.run(_run())
    assert line_count == 20, (
        f"serialisation must not drop lines under contention; got {line_count}/20"
    )
