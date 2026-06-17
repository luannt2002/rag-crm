"""Pipeline auditor logger — JSONL append for ingest + query stages.

Per-bot per-day file at reports/pipeline_audit_<bot_id>_<YYYYMMDD>.jsonl.
Each event is one JSON line: ``{"ts","stage","event","data":{...}}``.

Toggle via ``DEFAULT_PIPELINE_AUDIT_LOGGER_ENABLED`` constant + the
``RAGBOT_PIPELINE_AUDIT_ENABLED`` env override (``true``/``false``).
Zero-impact when disabled — ``log()`` early-returns before any file IO.
Async-safe: a single ``asyncio.Lock`` per output path serialises writes
inside the same event loop. Cross-process serialisation is NOT needed
in dev/single-worker mode; in multi-worker prod each worker writes to
its own ``_partN`` suffix when the daily file crosses the size cap.

Default OFF — operator must opt-in to avoid surprise disk usage.
"""
from __future__ import annotations

import asyncio
import os
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path

import structlog

from ragbot.shared.json_io import dumps as json_dumps
from typing import Any

from ragbot.shared.constants import (
    DEFAULT_PIPELINE_AUDIT_LOGGER_ENABLED,
    DEFAULT_PIPELINE_AUDIT_LOG_DIR,
    DEFAULT_PIPELINE_AUDIT_MAX_FILE_BYTES,
    DEFAULT_PIPELINE_AUDIT_SERIALISE_ERROR_CAP,
)

logger = structlog.get_logger(__name__)

# Warn-not-raise: prometheus_client gauge is observability-only. JSONL
# audit writes still proceed; only the in-flight lock-count gauge goes
# dark. Sacred paths (retrieval / embed / answer) do NOT depend on this
# gauge, so degrade is tolerable. If you see this warning at startup,
# `pip install prometheus_client` to restore lock-count visibility.
try:  # pragma: no cover — optional metrics import (tests may not load app)
    from ragbot.infrastructure.observability.metrics import (
        inflight_locks_size,
    )
except ImportError as _metrics_exc:
    logger.warning(
        "feature_disabled_dep_missing",
        module="pipeline_audit_logger",
        feature="inflight_locks_gauge",
        missing_pkg="prometheus_client",
        degraded_to="no_lock_count_gauge",
        error=str(_metrics_exc)[:100],
    )
    inflight_locks_size = None  # type: ignore[assignment]


_ENV_TOGGLE = "RAGBOT_PIPELINE_AUDIT_ENABLED"


def _write_line_sync(path: Path, line: str) -> None:
    """Sync ``open()`` + append helper run under ``asyncio.to_thread``.

    Kept at module scope so the thread executor can pickle / dispatch
    the callable without capturing class state. Any ``OSError`` is
    re-raised so the caller's ``except`` reports it on stderr — the
    audit logger never propagates a disk error to the pipeline.
    """
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class PipelineAuditLogger:
    """Append-only JSONL logger for the ingest + query pipeline stages.

    Singleton-friendly: instantiate once via DI and pass into every
    service / node that needs to record an event. No DB, no Redis —
    intentionally local-only so an audit trail survives even when the
    main observability stack is down.
    """

    # Per-path locks (asyncio). Class-level so multiple Container-scoped
    # instances writing to the same file still serialise correctly.
    #
    # WeakValueDictionary so an unused per-file lock is garbage-collected
    # once no in-flight ``log()`` call holds a strong reference — prevents
    # unbounded growth across rotating daily files and high bot
    # cardinality (one path per bot per day, per rotation slot).
    _locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
        weakref.WeakValueDictionary()
    )

    def __init__(self, output_dir: str | None = None) -> None:
        self._output_dir = Path(output_dir or DEFAULT_PIPELINE_AUDIT_LOG_DIR)
        # Ensure dir exists at construction so the first ``log()`` call
        # does not race on mkdir under concurrent ingest.
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def is_enabled(cls) -> bool:
        """Resolve enable flag — env override wins over the constant."""
        env = os.environ.get(_ENV_TOGGLE, "").strip().lower()
        if env in ("true", "1", "yes", "on"):
            return True
        if env in ("false", "0", "no", "off"):
            return False
        return bool(DEFAULT_PIPELINE_AUDIT_LOGGER_ENABLED)

    def _resolve_path(self, bot_id: str) -> Path:
        """Build daily file path; rotate to ``_partN`` when size cap hit."""
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        # Sanitise bot_id for filesystem safety (UUIDs and slugs are fine,
        # but caller may pass arbitrary strings during smoke tests).
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(bot_id))
        base = self._output_dir / f"pipeline_audit_{safe}_{date}.jsonl"
        if not base.exists():
            return base
        try:
            if base.stat().st_size < DEFAULT_PIPELINE_AUDIT_MAX_FILE_BYTES:
                return base
        except OSError:
            return base
        # Roll forward _part2, _part3, ... until we find a slot under the cap.
        part = 2
        while True:
            rolled = self._output_dir / f"pipeline_audit_{safe}_{date}_part{part}.jsonl"
            if not rolled.exists():
                return rolled
            try:
                if rolled.stat().st_size < DEFAULT_PIPELINE_AUDIT_MAX_FILE_BYTES:
                    return rolled
            except OSError:
                return rolled
            part += 1

    async def log(
        self,
        bot_id: str,
        stage: str,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append one JSON line. No-op when disabled.

        Failures (disk full, permission) are swallowed with a stderr
        message — the auditor MUST NOT break the pipeline it audits.
        """
        if not self.is_enabled():
            return
        path = self._resolve_path(bot_id)
        record = {
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "event": event,
            "bot_id": str(bot_id),
            "data": data or {},
        }
        try:
            # orjson emits UTF-8 natively (equivalent of ensure_ascii=False);
            # ``default=str`` mirrors the stdlib fallback for non-JSON types.
            line = json_dumps(record, default=str)
        except (TypeError, ValueError):
            # Last-resort: stringify the data so the trace at least
            # records the event name + a hint of the payload.
            line = json_dumps(
                {
                    **record,
                    "data": {
                        "_serialise_error": str(record["data"])[
                            :DEFAULT_PIPELINE_AUDIT_SERIALISE_ERROR_CAP
                        ]
                    },
                },
            )
        # WeakValueDictionary.setdefault binds a strong ref via ``lock``
        # for the duration of ``async with`` — the entry stays alive
        # while held, and is collected once the block exits and no
        # other concurrent caller holds the same ref.
        path_key = str(path)
        lock = self._locks.get(path_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[path_key] = lock
        if inflight_locks_size is not None:
            try:
                inflight_locks_size.labels(pool="pipeline_audit").set(
                    len(self._locks),
                )
            except (ValueError, RuntimeError):
                pass
        async with lock:
            try:
                # Finding #13 perf fix: sync ``open().write()`` was blocking
                # the event loop for the duration of the fsync. Offload to
                # the default thread executor so concurrent chat / ingest
                # turns are not starved while one audit line writes.
                await asyncio.to_thread(_write_line_sync, path, line)
            except OSError as exc:  # noqa: BLE001 — observability never breaks pipeline
                # Use stderr instead of structlog to avoid recursive
                # logging if structlog itself routes here later.
                import sys as _sys

                print(
                    f"[pipeline_audit_logger] write failed path={path} err={exc}",
                    file=_sys.stderr,
                )

    async def log_safe(
        self,
        bot_id: str | None,
        stage: str,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """``log()`` variant tolerant of ``bot_id is None`` (early-stage callers).

        Falls back to the literal string ``"unknown"`` so the file still
        gets written and ops can spot orphan events. Useful at the very
        first node where state may not yet have a resolved bot.
        """
        await self.log(str(bot_id) if bot_id is not None else "unknown", stage, event, data)


__all__ = ["PipelineAuditLogger"]
