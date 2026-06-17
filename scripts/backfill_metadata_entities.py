"""Backfill metadata_json.entities for all chunks (Plan 260604-metadata-aware-v4).

Calls GenericLLMMetadataExtractor on chunk_context (or content fallback)
and stores extracted entities in document_chunks.metadata_json so query-side
filter `metadata_json @> {"entities": ["X"]}` can match.

Idempotent: skip chunks that already have non-empty metadata_json.entities.
Bounded concurrency via asyncio.Semaphore (DEFAULT_METADATA_INGEST_CONCURRENCY).

Usage:
    python3 scripts/backfill_metadata_entities.py [--bot-id SLUG] [--dry-run] [--limit N]

Cost estimate: ~$0.001 × N chunks × 1-2s/chunk.
For 544 chunks: ~$0.5, ~10-15 min wall clock with N=8 parallelism.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Bootstrap sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Load .env
ENV = ROOT / ".env"
for line in ENV.read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"')

import asyncpg  # noqa: E402
import litellm  # noqa: E402
import structlog  # noqa: E402

from ragbot.infrastructure.metadata_filter.generic_llm_extractor import (  # noqa: E402
    GenericLLMMetadataExtractor,
)
from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_METADATA_INGEST_CONCURRENCY,
)

logger = structlog.get_logger(__name__)


async def _resolve_extractor(conn) -> GenericLLMMetadataExtractor:
    """Build extractor from DB-resolved model + prompt (zero hardcode)."""
    model_id = await conn.fetchval(
        "SELECT value FROM system_config WHERE key='metadata_extraction_model'"
    )
    if not model_id:
        raise RuntimeError("system_config.metadata_extraction_model not seeded")
    prompt = await conn.fetchval(
        "SELECT content FROM language_packs "
        "WHERE code='vi' AND prompt_key='metadata_extract_default'"
    )
    if not prompt:
        raise RuntimeError("language_packs.metadata_extract_default not seeded")
    return GenericLLMMetadataExtractor(
        litellm_module=litellm,
        model_id=str(model_id).strip('"'),
        prompt_template=prompt,
        cache=None,  # no cache during batch backfill
    )


async def _list_chunks_to_backfill(conn, args):
    """Get chunks needing metadata.entities backfill.

    Idempotent: chunks already with non-empty metadata_json.entities are skipped.
    """
    where_bot = ""
    params = []
    if args.bot_id:
        bot_id = await conn.fetchval(
            "SELECT id FROM bots WHERE bot_id=$1 AND channel_type='web' AND is_deleted=false",
            args.bot_id,
        )
        if not bot_id:
            raise SystemExit(f"Bot {args.bot_id} not found")
        where_bot = "AND record_bot_id = $1"
        params.append(bot_id)

    limit = args.limit or 1000
    # Use content (raw VI) NOT chunk_context (may be English narrate)
    # to extract entities — ensures entities match user Vietnamese queries.
    # --force flag re-extracts even chunks with existing entities (fix English bias).
    force_clause = "" if args.force else """AND (
        metadata_json IS NULL
        OR metadata_json->>'entities' IS NULL
        OR jsonb_array_length(COALESCE(metadata_json->'entities', '[]'::jsonb)) = 0
    )"""
    sql = f"""
        SELECT id, content AS extract_text, content
        FROM document_chunks
        WHERE doc_deleted_at IS NULL
          {where_bot}
          {force_clause}
        ORDER BY id
        LIMIT {limit}
    """
    return await conn.fetch(sql, *params)


async def _process_chunk(
    extractor: GenericLLMMetadataExtractor,
    conn,
    chunk_id,
    extract_text: str,
    sem: asyncio.Semaphore,
    dry_run: bool,
) -> bool:
    """Extract + persist metadata for 1 chunk. Returns True if updated."""
    async with sem:
        # Use first 2000 chars from content (Vietnamese raw, NOT chunk_context
        # which may be English narrate). 2000 chars covers most chunks fully.
        sample = extract_text[:2000].strip()
        if not sample:
            return False
        result = await extractor.extract(sample, locale="vi")
        if not result or not result.get("entities"):
            return False
        # to_filter_dict already lowercase + clamp 3; cap 5 for storage
        entities = result["entities"][:5]
        # extractor.to_filter_dict returns already-lowercased entities;
        # result["entities"] from extract() comes from to_filter_dict so lowercased.

        if dry_run:
            print(f"  [DRY] {str(chunk_id)[:8]} entities={entities}")
            return True

        # Merge into existing metadata_json (preserve other keys)
        await conn.execute(
            """
            UPDATE document_chunks
            SET metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_build_object('entities', $2::jsonb)
            WHERE id = $1
            """,
            chunk_id,
            json.dumps(entities),
        )
        return True


async def main(args):
    db_url = os.getenv("DATABASE_URL").replace("+asyncpg", "").replace("postgresql+psycopg", "postgresql")
    conn = await asyncpg.connect(db_url)
    extractor = await _resolve_extractor(conn)

    chunks = await _list_chunks_to_backfill(conn, args)
    total = len(chunks)
    print(f"Backfill scope: {total} chunks (dry-run={args.dry_run})")
    if total == 0:
        print("Nothing to backfill ✅")
        await conn.close()
        return

    sem = asyncio.Semaphore(DEFAULT_METADATA_INGEST_CONCURRENCY)

    n_updated = 0
    n_failed = 0
    batch_size = 20
    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]
        results = await asyncio.gather(
            *[
                _process_chunk(
                    extractor, conn, c["id"], c["extract_text"] or c["content"],
                    sem, args.dry_run,
                )
                for c in batch
            ],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                n_failed += 1
            elif r:
                n_updated += 1
        print(f"  Progress: {min(i + batch_size, total)}/{total} | "
              f"updated={n_updated} failed={n_failed}")

    print()
    print(f"=== DONE === updated={n_updated}/{total} | failed={n_failed}")
    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-id", help="Restrict to single bot slug")
    parser.add_argument("--dry-run", action="store_true", help="Show entities without writing")
    parser.add_argument("--force", action="store_true", help="Re-extract even if entities exist (fix English bias)")
    parser.add_argument("--limit", type=int, default=1000, help="Max chunks to process")
    asyncio.run(main(parser.parse_args()))
