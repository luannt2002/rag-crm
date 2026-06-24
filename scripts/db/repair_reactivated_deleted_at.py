"""One-time repair — clear stale ``deleted_at`` on re-ingested (live) documents.

Before the state-flip fix (``_stage_finalize`` now clears ``deleted_at`` when a
doc flips to ``active``), re-ingesting a previously soft-deleted ``doc_id`` left
the row ``state='active'`` + live chunks (retrievable, answering) yet
``deleted_at IS NOT NULL`` — invisible to the ``deleted_at IS NULL`` document
count the demo UI shows (bug: "tài liệu = 0" for bots that still answer).

Targets ONLY genuinely-reactivated docs: ``state='active'`` AND
``deleted_at IS NOT NULL`` AND ``updated_at > deleted_at`` (the ingest flip
touched ``updated_at`` AFTER the soft-delete). Genuinely-deleted docs
(``updated_at <= deleted_at``, never re-ingested) are left untouched.

Idempotent. Dry-run by default; ``--apply`` to clear.

    set -a && source .env && set +a
    python scripts/db/repair_reactivated_deleted_at.py            # dry-run
    python scripts/db/repair_reactivated_deleted_at.py --apply    # clear
"""
from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_SELECT = """
    SELECT d.id, b.bot_id, d.document_name, d.deleted_at, d.updated_at,
           (SELECT count(*) FROM document_chunks dc WHERE dc.record_document_id=d.id) AS chunks
    FROM documents d JOIN bots b ON b.id = d.record_bot_id
    WHERE d.state = 'active'
      AND d.deleted_at IS NOT NULL
      AND d.updated_at > d.deleted_at
    ORDER BY b.bot_id, d.document_name
"""

_UPDATE = """
    UPDATE documents SET deleted_at = NULL, updated_at = now()
    WHERE state = 'active'
      AND deleted_at IS NOT NULL
      AND updated_at > deleted_at
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    args = ap.parse_args()

    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as conn:
        rows = (await conn.execute(text(_SELECT))).fetchall()

    print("=== reactivated-but-soft-deleted documents (live, hidden from UI) ===")
    if not rows:
        print("  none — nothing to repair.")
        await eng.dispose()
        return
    for r in rows:
        print(f"  {r.bot_id:28s} {str(r.document_name)[:30]:30s} chunks={r.chunks}")
    print(f"\nAffected: {len(rows)} document(s)")

    if not args.apply:
        print("\nDRY-RUN — no changes. Re-run with --apply to clear deleted_at.")
        await eng.dispose()
        return

    async with eng.begin() as conn:
        result = await conn.execute(text(_UPDATE))
    print(f"\n=== APPLIED — cleared deleted_at on {result.rowcount} document(s) ===")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
