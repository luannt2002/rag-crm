"""Purge conversations older than DEFAULT_CONVERSATION_RETENTION_DAYS.

Nightly cron entry (example):
    0 3 * * *  /path/to/venv/bin/python /path/to/repo/scripts/purge_expired_conversations.py

CLI:
    --dry-run   Print row count that WOULD be deleted; no DELETE executed.
    --days N    Override retention window. Resolution order:
                    1. --days CLI flag
                    2. system_config key "conversation_retention_days"
                    3. shared.constants.DEFAULT_CONVERSATION_RETENTION_DAYS
    --batch N   Batch size per DELETE loop (default 10_000).

Column choice:
    The conversations table in src/ragbot/infrastructure/db/models.py has
    `last_message_at` (indexed, auto-set on insert, updated on activity) and
    `created_at`. We purge on `last_message_at` — it reflects *recent activity*
    and better matches "idle conversation" retention semantics. There is no
    `updated_at` column on this table; `last_message_at` is the equivalent.

BATCH 2 audit closure:
    S4 — "No TTL on conversations table. Rows grow unbounded."

Script is idempotent: re-running immediately deletes 0 rows. Raw SQL
(parameterised) via SQLAlchemy core — no ORM session, no domain imports.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

# Allow running as a standalone script (cron will invoke via absolute path).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from ragbot.shared.constants import DEFAULT_CONVERSATION_RETENTION_DAYS  # noqa: E402

DEFAULT_BATCH_SIZE = 10_000
_SYSTEM_CONFIG_KEY = "conversation_retention_days"


def _compute_cutoff(days: int) -> datetime:
    """Return cutoff UTC datetime = now - days."""
    if days <= 0:
        raise ValueError(f"retention days must be > 0, got {days}")
    return datetime.now(tz=timezone.utc) - timedelta(days=days)


def _resolve_retention_days(
    system_config: dict[str, Any], cli_days: int | None = None
) -> int:
    """Resolve retention days: CLI > system_config > constants default.

    system_config is a plain dict (caller-loaded); we look up
    `conversation_retention_days`. Missing / invalid → fall back.
    """
    if cli_days is not None:
        return int(cli_days)
    raw = system_config.get(_SYSTEM_CONFIG_KEY)
    if raw is not None:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    return DEFAULT_CONVERSATION_RETENTION_DAYS


def _database_url() -> str:
    url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DB env missing: set DATABASE_URL_SYNC (or DATABASE_URL)"
        )
    # SQLAlchemy sync drivers: normalise to psycopg2 if driverless.
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url


def _load_system_config(engine: Any) -> dict[str, Any]:
    """Load system_config rows into a flat dict. Silent on table missing."""
    from sqlalchemy import text  # local import — avoid cost if mocked out

    cfg: dict[str, Any] = {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT key, value FROM system_config")
            ).fetchall()
        for key, value in rows:
            cfg[key] = value
    except Exception:  # noqa: BLE001 — system_config table optional on fresh DB (safe fallback)
        # Table may not exist on fresh installs; default-fall-through is fine.
        return {}
    return cfg


def _count_expired(engine: Any, cutoff: datetime) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM conversations "
                "WHERE last_message_at < :cutoff"
            ).bindparams(cutoff=cutoff)
        ).scalar()
    return int(result or 0)


def _delete_batched(engine: Any, cutoff: datetime, batch_size: int) -> int:
    """Delete in batches of `batch_size`. Returns total rows deleted."""
    from sqlalchemy import text

    total = 0
    stmt = text(
        "DELETE FROM conversations "
        "WHERE id IN ( "
        "  SELECT id FROM conversations "
        "  WHERE last_message_at < :cutoff "
        "  LIMIT :lim "
        ")"
    )
    while True:
        with engine.begin() as conn:
            result = conn.execute(
                stmt.bindparams(cutoff=cutoff, lim=batch_size)
            )
            deleted = result.rowcount or 0
        total += deleted
        if deleted < batch_size:
            break
    return total


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str))


def _db_host(url: str) -> str:
    try:
        return urlparse(
            url.replace("postgresql+psycopg2://", "postgresql://")
        ).hostname or "?"
    except Exception:  # noqa: BLE001 — malformed DB URL parsing (defensive fallback)
        return "?"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge conversations older than retention window."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)

    started = time.monotonic()
    try:
        url = _database_url()
        from sqlalchemy import create_engine  # local import for test-mock ease

        engine = create_engine(url, future=True)
    except Exception as exc:
        _emit(
            {
                "event": "conversations_purge_error",
                "stage": "connect",
                "error": str(exc),
            }
        )
        return 1

    try:
        sys_cfg = _load_system_config(engine)
        days = _resolve_retention_days(sys_cfg, cli_days=args.days)
        cutoff = _compute_cutoff(days)

        expired = _count_expired(engine, cutoff)

        if args.dry_run:
            _emit(
                {
                    "event": "conversations_purge_dry_run",
                    "would_delete": expired,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": days,
                    "db_host": _db_host(url),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
            return 0

        deleted = _delete_batched(engine, cutoff, args.batch)
        _emit(
            {
                "event": "conversations_purged",
                "deleted_count": deleted,
                "cutoff": cutoff.isoformat(),
                "retention_days": days,
                "batch_size": args.batch,
                "db_host": _db_host(url),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
        return 0
    except Exception as exc:
        _emit(
            {
                "event": "conversations_purge_error",
                "stage": "execute",
                "error": str(exc),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
        return 1
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
