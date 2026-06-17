#!/usr/bin/env python3
"""Post-ingest cross-doc chunk dedup — drops near-duplicates within one bot."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import asyncpg

from ragbot.shared.constants import (
    DEFAULT_DEDUP_JACCARD_THRESHOLD,
    DEFAULT_DEDUP_MIN_CHARS,
)
from ragbot.shared.dedup import find_duplicate_pairs


async def fetch_chunks(dsn: str, bot_uuid: str) -> list[dict[str, Any]]:
    """Fetch all non-deleted chunks of one bot for pairwise dedup analysis."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT dc.id, dc.content, dc.created_at, d.document_name "
            "FROM document_chunks dc "
            "JOIN documents d ON dc.record_document_id = d.id "
            "WHERE d.record_bot_id = $1 AND d.deleted_at IS NULL",
            bot_uuid,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def soft_delete(dsn: str, ids: list[str]) -> int:
    """Mark chunks as deduped via metadata_json flag; returns affected row count."""
    if not ids:
        return 0
    conn = await asyncpg.connect(dsn)
    try:
        result = await conn.execute(
            "UPDATE document_chunks "
            "SET metadata_json = jsonb_set("
            "  COALESCE(metadata_json, '{}'::jsonb), '{deduped}', 'true'::jsonb"
            ") WHERE id = ANY($1::uuid[])",
            ids,
        )
        return int(result.split()[-1])
    finally:
        await conn.close()


async def main() -> int:
    """Entry point: dry-run dedup report, optional --apply soft-delete."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bot-uuid", required=True, help="record_bot_id (UUID PK of bots)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_DEDUP_JACCARD_THRESHOLD)
    ap.add_argument("--min-chars", type=int, default=DEFAULT_DEDUP_MIN_CHARS)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Apply soft-delete; default is DRY-RUN report only",
    )
    args = ap.parse_args()

    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not dsn_raw:
        print("DATABASE_URL_SYNC or DATABASE_URL env required", file=sys.stderr)
        return 2
    dsn = dsn_raw.replace("+psycopg2", "").replace("+asyncpg", "")

    chunks = await fetch_chunks(dsn, args.bot_uuid)
    pairs = find_duplicate_pairs(
        chunks, threshold=args.threshold, min_chars=args.min_chars,
    )

    print(f"Bot: {args.bot_uuid}")
    print(f"Total chunks: {len(chunks)}")
    print(f"Duplicate pairs (Jaccard >= {args.threshold}): {len(pairs)}")
    drop_ids = sorted({pid[1] for pid in pairs})
    print(f"Unique chunks to drop: {len(drop_ids)}")

    if args.apply:
        n = await soft_delete(dsn, drop_ids)
        print(f"Marked {n} chunks deduped (metadata_json.deduped=true)")
    else:
        print("DRY-RUN — pass --apply to soft-delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
