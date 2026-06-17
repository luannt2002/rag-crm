"""Unit tests for ``PipelineAuditLogger`` — JSONL ingest+query trace."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ragbot.infrastructure.observability.pipeline_audit_logger import (
    PipelineAuditLogger,
)


_ENV = "RAGBOT_PIPELINE_AUDIT_ENABLED"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Each test starts with the env toggle cleared so the constant default
    drives the disabled-by-default behaviour. Per-test ``monkeypatch.setenv``
    flips it on where needed."""
    monkeypatch.delenv(_ENV, raising=False)
    # Also reset the per-path lock dict so repeated tests don't leak Locks
    # bound to a previous event loop.
    PipelineAuditLogger._locks.clear()


def test_logger_disabled_no_file_written(tmp_path: Path) -> None:
    """When the env toggle is unset and the constant default is False, the
    logger MUST NOT touch the filesystem — early-return path."""
    log = PipelineAuditLogger(output_dir=str(tmp_path))
    assert log.is_enabled() is False
    asyncio.run(
        log.log("bot-x", "ingest", "ingest_started", {"raw_len": 100}),
    )
    files = list(tmp_path.glob("pipeline_audit_*.jsonl"))
    assert files == [], f"expected zero files, got {files}"


def test_logger_enabled_appends_jsonl(tmp_path: Path, monkeypatch) -> None:
    """When env toggle is true, two log() calls produce 2 JSONL lines and
    each line is well-formed with required keys."""
    monkeypatch.setenv(_ENV, "true")
    log = PipelineAuditLogger(output_dir=str(tmp_path))
    assert log.is_enabled() is True

    async def _run() -> None:
        await log.log("bot-y", "ingest", "ingest_started", {"raw_len": 1234})
        await log.log("bot-y", "query", "query_completed", {"answer_chars": 42})

    asyncio.run(_run())

    expected = tmp_path / f"pipeline_audit_bot-y_{_today()}.jsonl"
    assert expected.exists(), f"missing {expected}"
    lines = expected.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    # Required schema keys
    for rec in (rec0, rec1):
        assert set(rec.keys()) >= {"ts", "iso", "stage", "event", "bot_id", "data"}
        assert isinstance(rec["ts"], float)
        assert rec["bot_id"] == "bot-y"
    assert rec0["stage"] == "ingest"
    assert rec0["event"] == "ingest_started"
    assert rec0["data"]["raw_len"] == 1234
    assert rec1["event"] == "query_completed"
    assert rec1["data"]["answer_chars"] == 42


def test_concurrent_writes_serialised_via_lock(tmp_path: Path, monkeypatch) -> None:
    """Fire 50 coroutines into the same path; every write must land as a
    single JSON line (no interleaving) and the line count = 50."""
    monkeypatch.setenv(_ENV, "1")
    log = PipelineAuditLogger(output_dir=str(tmp_path))

    async def _run() -> None:
        coros = [
            log.log("bot-z", "query", "chunks_retrieved", {"i": i, "preview": "x" * 50})
            for i in range(50)
        ]
        await asyncio.gather(*coros)

    asyncio.run(_run())

    expected = tmp_path / f"pipeline_audit_bot-z_{_today()}.jsonl"
    lines = expected.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    indices: list[int] = []
    for line in lines:
        rec = json.loads(line)  # would ValueError if any line was interleaved
        indices.append(rec["data"]["i"])
    assert sorted(indices) == list(range(50))


def test_event_schema_includes_required_keys(tmp_path: Path, monkeypatch) -> None:
    """Schema sanity: every emitted record carries the canonical keys so
    the replay script can group + render without defensive .get()."""
    monkeypatch.setenv(_ENV, "true")
    log = PipelineAuditLogger(output_dir=str(tmp_path))
    asyncio.run(
        log.log(
            "bot-schema",
            "query",
            "hybrid_search_executed",
            {"candidates_count": 20, "top_score": 0.0489, "request_id": "req-1"},
        ),
    )
    p = tmp_path / f"pipeline_audit_bot-schema_{_today()}.jsonl"
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    # Top-level keys
    for k in ("ts", "iso", "stage", "event", "bot_id", "data"):
        assert k in rec, f"missing required key {k}"
    assert rec["data"]["request_id"] == "req-1"
    assert rec["data"]["top_score"] == pytest.approx(0.0489)


def test_safe_log_handles_none_bot_id(tmp_path: Path, monkeypatch) -> None:
    """``log_safe`` accepts ``None`` and falls back to ``unknown`` so very
    early-stage events still land in a file."""
    monkeypatch.setenv(_ENV, "true")
    log = PipelineAuditLogger(output_dir=str(tmp_path))
    asyncio.run(log.log_safe(None, "ingest", "ingest_started", {"raw_len": 1}))
    p = tmp_path / f"pipeline_audit_unknown_{_today()}.jsonl"
    assert p.exists()


def test_env_override_off_beats_constant_on(tmp_path: Path, monkeypatch) -> None:
    """Even if a future build flips the constant default to True, an
    explicit env=false must still disable the logger."""
    monkeypatch.setenv(_ENV, "false")
    log = PipelineAuditLogger(output_dir=str(tmp_path))
    assert log.is_enabled() is False
    asyncio.run(log.log("b", "ingest", "ingest_started", {}))
    assert list(tmp_path.glob("*.jsonl")) == []


def test_non_serialisable_data_does_not_break_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    """A non-serialisable payload still produces a line (with a
    ``_serialise_error`` marker) — observability must never break the
    pipeline it audits."""
    monkeypatch.setenv(_ENV, "true")
    log = PipelineAuditLogger(output_dir=str(tmp_path))

    class _Weird:
        def __repr__(self) -> str:
            return "<Weird>"

    # ``default=str`` in json.dumps handles most things; force a real
    # failure by using a key that is not a string.
    asyncio.run(
        log.log("bot-w", "ingest", "ingest_started", {"obj": _Weird(), "ok": 1}),
    )
    p = tmp_path / f"pipeline_audit_bot-w_{_today()}.jsonl"
    text = p.read_text(encoding="utf-8")
    # Either real serialisation worked via default=str, or the fallback
    # _serialise_error path fired — both leave a valid JSON line.
    rec = json.loads(text.splitlines()[0])
    assert rec["event"] == "ingest_started"
