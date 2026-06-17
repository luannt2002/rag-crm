#!/usr/bin/env python3
"""Operator-side bot-owner DB UPDATE on bots.system_prompt.

⚠️ DEPRECATED — VI PHẠM CLAUDE.md Application MINDSET rule 7 (2026-05-25).
See ``scripts/owner_action/02_sysprompt_loosen.sh`` header for the full
incident write-up (Wave M3.6-L2 → K1 bug 2026-05-25). Path forward:
admin UI edit (audit_log trail) or alembic migration. This module stays
on disk for emergency rollback reference only.

Steps: resolve 3-key → backup current sysprompt → UPDATE row → bust Redis cache.
3-key (tenant_id, bot_id, channel_type) all REQUIRED per identity rule.

Env:
    LOADTEST_TENANT_ID, LOADTEST_BOT_ID, LOADTEST_CHANNEL_TYPE — required.
    DATABASE_URL_SYNC | DATABASE_URL — required.
    REDIS_URL, SYSPROMPT_TXT_PATH, SYSPROMPT_BACKUP_DIR — optional.

Exit: 0 success | 1 precondition | 2 3-key unresolved | 3 update rowcount != 1.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _coerce_dsn_to_psycopg2(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg2://"):
        return "postgresql://" + dsn[len("postgresql+psycopg2://"):]
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://"):]
    return dsn


def _log(msg: str) -> None:
    """Stdout-only structured-ish log line; prefix with timestamp."""
    sys.stdout.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} | 02b | {msg}\n")
    sys.stdout.flush()


def _bust_redis(redis_url: str, registry_key: str, sysprompt_key: str) -> None:
    """Best-effort cache bust; logs warning instead of failing the run."""
    try:
        import redis  # type: ignore[import-not-found]

        r = redis.from_url(redis_url)
        n1 = r.delete(registry_key)
        n2 = r.delete(sysprompt_key)
        _log(f"redis_bust registry_deleted={n1} sysprompt_deleted={n2}")
    except ImportError:
        _log("WARN redis library not installed — caches will expire on TTL")
    except Exception as exc:  # noqa: BLE001 — best-effort cache bust, never block apply
        _log(f"WARN redis_bust_failed err={type(exc).__name__}: {exc}")


def main() -> int:
    # 3-key REQUIRED (identity rule).
    tenant_id = os.environ.get("LOADTEST_TENANT_ID")
    bot_id = os.environ.get("LOADTEST_BOT_ID")
    channel_type = os.environ.get("LOADTEST_CHANNEL_TYPE")
    if not (tenant_id and bot_id and channel_type):
        _log("ERROR 3-key REQUIRED: set LOADTEST_TENANT_ID, LOADTEST_BOT_ID, LOADTEST_CHANNEL_TYPE")
        return 1
    try:
        tenant_id_int = int(tenant_id)
    except ValueError:
        _log(f"ERROR LOADTEST_TENANT_ID must be int, got {tenant_id!r}")
        return 1

    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not dsn_raw:
        _log("ERROR DATABASE_URL_SYNC or DATABASE_URL required")
        return 1
    dsn = _coerce_dsn_to_psycopg2(dsn_raw)

    here = Path(__file__).resolve().parent
    sysprompt_path_str = os.environ.get(
        "SYSPROMPT_TXT_PATH", str(here / "02b_sysprompt_loosen.txt"),
    )
    sysprompt_path = Path(sysprompt_path_str)
    if not sysprompt_path.is_file():
        _log(f"ERROR sysprompt_file_not_found path={sysprompt_path}")
        return 1
    new_sp = sysprompt_path.read_text(encoding="utf-8")
    new_chars = len(new_sp)
    _log(f"new_sysprompt_loaded path={sysprompt_path} chars={new_chars}")

    backup_dir = Path(os.environ.get("SYSPROMPT_BACKUP_DIR", "/tmp"))
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        _log("ERROR psycopg2 not installed — install requirements.txt first")
        return 1

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, COALESCE(system_prompt,'') FROM bots "
                "WHERE tenant_id=%s AND bot_id=%s AND channel_type=%s "
                "  AND is_deleted=false",
                (tenant_id_int, bot_id, channel_type),
            )
            row = cur.fetchone()
        if not row:
            _log(
                f"ERROR bot_3key_unresolved tenant={tenant_id_int} "
                f"bot_id={bot_id} channel={channel_type}"
            )
            return 2
        record_bot_id, current_sp = row
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_file = (
            backup_dir
            / f"sysprompt_backup_{tenant_id_int}_{bot_id}_{channel_type}_{ts}.txt"
        )
        backup_file.write_text(current_sp, encoding="utf-8")
        _log(
            f"bot_resolved record_bot_id={record_bot_id} "
            f"current_chars={len(current_sp)} backup={backup_file}"
        )

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bots SET system_prompt=%s, updated_at=now() "
                "WHERE tenant_id=%s AND bot_id=%s AND channel_type=%s "
                "  AND is_deleted=false",
                (new_sp, tenant_id_int, bot_id, channel_type),
            )
            rc = cur.rowcount
        conn.commit()
        _log(f"update_rowcount={rc}")
        if rc != 1:
            _log(f"ERROR update_rowcount_not_1 ({rc}) — investigate; backup={backup_file}")
            return 3
    finally:
        conn.close()

    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/1")
    registry_key = f"ragbot:bot:{tenant_id_int}:{bot_id}:{channel_type}"
    sysprompt_key = f"ragbot:sysprompt:{record_bot_id}"
    _bust_redis(redis_url, registry_key, sysprompt_key)

    _log(f"DONE sysprompt updated chars={new_chars} backup={backup_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
