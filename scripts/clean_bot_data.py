#!/usr/bin/env python3
"""clean_bot_data.py — wipe corpus + caches for ONE bot, keep audit trail.

Use case: load test from a fresh corpus state. Deletes documents +
chunks + semantic-cache entries scoped to ``record_bot_id``, then
flushes Redis cache keys for the same bot. KEEPs ``request_logs``,
``audit_log``, ``conversations``, ``messages`` so historical analytics
+ forensic trail stay intact.

Idempotent — running twice leaves same final state. Safe to abort
mid-run; subsequent run picks up remaining rows.

Usage::

    python scripts/clean_bot_data.py \\
        --bot-id 1774946011723 --tenant-id 32 --channel-type web \\
        --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))


# Per-bot tables wiped directly via record_bot_id. ``document_chunks``
# does NOT carry record_bot_id — it cascades from ``documents`` via the
# FK ``fk_chunks_document ON DELETE CASCADE``, so deleting documents
# also empties chunks. We still verify post-count = 0 for chunks via a
# JOIN against the now-empty documents table.
TABLES_TO_WIPE = (
    # (table, scope_column, comment)
    ("documents", "record_bot_id", "ingested source docs (CASCADE wipes chunks)"),
    ("semantic_cache", "record_bot_id", "exact + semantic answer cache"),
)

# Verify-only: chunks should be empty after documents cascade.
CASCADE_VERIFY_TABLES = (
    ("document_chunks", "vector index per bot (cascades from documents)"),
)

# Redis key patterns (glob) flushed for this bot.
REDIS_PATTERNS = (
    "ragbot:bot:*:{bot_id}:*",
    "ragbot:exact:{bot_id}:*",
    "ragbot:semantic:{bot_id}:*",
    "ragbot:retrieval:{bot_id}:*",
    "ragbot:bot_config:{bot_id}:*",
)


async def _resolve_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL env var required")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


async def _resolve_record_bot_id(engine: object, bot_id: str, channel_type: str) -> UUID | None:
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = await conn.execute(
            text("""
                SELECT id FROM bots
                WHERE bot_id = :b AND channel_type = :c
                LIMIT 1
            """),
            {"b": bot_id, "c": channel_type},
        )
        rec = row.fetchone()
        return rec[0] if rec else None


async def _count_rows(engine: object, table: str, record_bot_id: UUID) -> int:
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = await conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE record_bot_id = :b"),
            {"b": record_bot_id},
        )
        return int(row.scalar_one())


async def _count_chunks_for_bot(engine: object, record_bot_id: UUID) -> int:
    """document_chunks doesn't carry record_bot_id; join via documents.id."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = await conn.execute(
            text("""
                SELECT COUNT(*)
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.record_document_id
                WHERE d.record_bot_id = :b
            """),
            {"b": record_bot_id},
        )
        return int(row.scalar_one())


async def _delete_rows(engine: object, table: str, record_bot_id: UUID) -> int:
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"DELETE FROM {table} WHERE record_bot_id = :b"),
            {"b": record_bot_id},
        )
        return result.rowcount or 0


async def _flush_redis(bot_id: str) -> dict[str, int]:
    """Connect to Redis using REDIS_URL env, scan + delete matching keys."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        from redis import asyncio as aioredis  # type: ignore
    except ImportError:  # pragma: no cover — repo always installs redis
        print("[redis] redis-py missing, skipping Redis flush", file=sys.stderr)
        return {p: -1 for p in REDIS_PATTERNS}

    client = aioredis.from_url(redis_url, decode_responses=True)
    deleted: dict[str, int] = {}
    try:
        for pattern_template in REDIS_PATTERNS:
            pattern = pattern_template.format(bot_id=bot_id)
            count = 0
            cursor: int = 0
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=200)
                if keys:
                    count += await client.delete(*keys)
                if cursor == 0:
                    break
            deleted[pattern] = count
    finally:
        await client.aclose()
    return deleted


async def _amain(args: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = await _resolve_db_url()
    engine = create_async_engine(db_url, echo=False)
    try:
        record_bot_id = await _resolve_record_bot_id(
            engine, args.bot_id, args.channel_type
        )
        if record_bot_id is None:
            print(
                f"ERROR: bot not found: bot_id={args.bot_id} "
                f"channel_type={args.channel_type}",
                file=sys.stderr,
            )
            return 2
        print(f"[resolve] record_bot_id = {record_bot_id}", flush=True)

        # Pre-count
        print("[pre-count] rows by table:", flush=True)
        for table, _, comment in TABLES_TO_WIPE:
            n = await _count_rows(engine, table, record_bot_id)
            print(f"  - {table:20s} {n:>8} rows  ({comment})", flush=True)
        for table, comment in CASCADE_VERIFY_TABLES:
            n = await _count_chunks_for_bot(engine, record_bot_id)
            print(f"  - {table:20s} {n:>8} rows  ({comment})", flush=True)

        if not args.confirm:
            print(
                "\n[DRY-RUN] add --confirm to actually delete. "
                "Aborting without changes.",
                flush=True,
            )
            return 0

        # Delete
        print("\n[delete] running...", flush=True)
        for table, _, _ in TABLES_TO_WIPE:
            n = await _delete_rows(engine, table, record_bot_id)
            print(f"  - {table:20s} deleted {n:>8} rows", flush=True)

        # Flush Redis
        print("\n[redis] flushing keys...", flush=True)
        redis_counts = await _flush_redis(args.bot_id)
        for pattern, n in redis_counts.items():
            print(f"  - {pattern!r:55s} deleted {n:>5} keys", flush=True)

        # Post-count verify
        print("\n[post-count] verify rows = 0:", flush=True)
        any_residual = False
        for table, _, _ in TABLES_TO_WIPE:
            n = await _count_rows(engine, table, record_bot_id)
            mark = "OK" if n == 0 else "RESIDUAL"
            if n > 0:
                any_residual = True
            print(f"  - {table:20s} {n:>8} rows  [{mark}]", flush=True)
        for table, _ in CASCADE_VERIFY_TABLES:
            n = await _count_chunks_for_bot(engine, record_bot_id)
            mark = "OK" if n == 0 else "RESIDUAL"
            if n > 0:
                any_residual = True
            print(f"  - {table:20s} {n:>8} rows  [{mark}]", flush=True)

        print("\n[done] clean complete.", flush=True)
        if any_residual:
            print(
                "[warn] residual rows present — possibly FK chain not fully unwound; "
                "investigate.",
                file=sys.stderr,
                flush=True,
            )
            return 1
        return 0
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Wipe corpus + caches for ONE bot.")
    p.add_argument("--bot-id", required=True)
    p.add_argument("--tenant-id", type=int, required=True, help="upstream int tenant_id (for log)")
    p.add_argument("--channel-type", required=True)
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually run DELETE. Without it, dry-run shows counts only.",
    )
    return p.parse_args()


def main() -> int:
    return asyncio.run(_amain(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
