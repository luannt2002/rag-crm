"""One-time cleanup — collapse duplicate ``document_service_index`` rows.

Before the idempotent-write fix (``_stage_finalize`` now ALWAYS deletes a
document's stats rows before re-inserting), an at-least-once redelivery of the
ingest task on a first-time ``doc_id`` (is_reindex=False) appended a full
duplicate copy of every entity each pass — leaving multipliers of 2x/3x/4x in
the live index (verified 2026-06-24). This script removes the accumulated
duplicates, keeping the NEWEST row per logical entity
``(record_document_id, entity_name, price_primary, price_secondary)`` — the same
identity key ``_dedup_stats_entities`` uses, so genuinely-distinct same-name /
different-price entities are preserved.

Idempotent: running it again after a clean pass deletes 0 rows. Dry-run by
default; pass ``--apply`` to execute.

    set -a && source .env && set +a
    python scripts/db/dedup_stats_index.py            # dry-run (report only)
    python scripts/db/dedup_stats_index.py --apply    # delete duplicates
"""
from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Rank copies of one logical entity; rn=1 is the newest (kept), rn>1 deleted.
_RANKED_CTE = """
    WITH ranked AS (
        SELECT id,
               record_document_id,
               ROW_NUMBER() OVER (
                   PARTITION BY record_document_id, entity_name,
                                price_primary, price_secondary
                   ORDER BY created_at DESC NULLS LAST, id DESC
               ) AS rn
        FROM document_service_index
    )
"""


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="execute the DELETE (default: dry-run report only)",
    )
    args = parser.parse_args()

    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as conn:
        before = (await conn.execute(text(
            """SELECT record_document_id, count(*) AS total,
                      count(DISTINCT (entity_name, price_primary, price_secondary)) AS distinct_ent
               FROM document_service_index
               GROUP BY record_document_id
               ORDER BY total DESC"""
        ))).fetchall()
        _count_sql = _RANKED_CTE + "SELECT count(*) FROM ranked WHERE rn > 1"  # noqa: S608 — constant CTE + literal, no user input
        dup_total = (await conn.execute(text(_count_sql))).scalar_one()

    print("=== BEFORE (per-document multiplier) ===")
    for doc_id, total, distinct_ent in before:
        mult = round(total / distinct_ent, 2) if distinct_ent else 0
        flag = "  <-- DUP" if mult > 1.0 else ""
        print(f"  {doc_id}  total={total:>4}  distinct={distinct_ent:>4}  mult={mult}{flag}")
    print(f"\nDuplicate rows to remove: {dup_total}")

    if not args.apply:
        print("\nDRY-RUN — no changes. Re-run with --apply to delete duplicates.")
        await eng.dispose()
        return

    # constant CTE + literal DELETE, no user input — S608 false positive.
    _delete_sql = (
        _RANKED_CTE  # noqa: S608
        + "DELETE FROM document_service_index "
        + "WHERE id IN (SELECT id FROM ranked WHERE rn > 1)"
    )
    async with eng.begin() as conn:
        result = await conn.execute(text(_delete_sql))
        deleted = result.rowcount

    async with eng.connect() as conn:
        after = (await conn.execute(text(
            """SELECT record_document_id, count(*) AS total,
                      count(DISTINCT (entity_name, price_primary, price_secondary)) AS distinct_ent
               FROM document_service_index
               GROUP BY record_document_id
               HAVING count(*) > count(DISTINCT (entity_name, price_primary, price_secondary))"""
        ))).fetchall()

    print(f"\n=== APPLIED — deleted {deleted} duplicate rows ===")
    if after:
        print("WARNING — docs still showing duplicates (investigate):")
        for doc_id, total, distinct_ent in after:
            print(f"  {doc_id}  total={total}  distinct={distinct_ent}")
    else:
        print("All documents now at multiplier 1.00 (no duplicates remain).")

    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
