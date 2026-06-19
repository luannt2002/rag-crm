"""Sprint 12C T1: documents.content_hash UNIQUE + BM25 search_vector backfill.

Closes 2 P0 bugs from chunking deep-dive audit 2026-04-29:

1. Sheet-1 == Sheet-4 IDENTICAL MD5 — there was no DB-level dedup on
   ``raw_content`` so identical Google Sheet exports landed twice. We add
   a partial UNIQUE constraint on ``(record_bot_id, content_hash) WHERE
   deleted_at IS NULL`` so soft-deletes can re-ingest the same content
   later but live duplicates are rejected. ``content_hash`` already
   exists (migration 0013); this migration only backfills any "pending"
   placeholders before tightening the constraint and only enforces
   uniqueness on live rows. Non-NULL is already guaranteed by 0013.

2. ``document_chunks.search_vector = NULL`` for 24/24 chunks — the
   trigger ``trg_chunk_search_vector`` is correct (migration 0028+0046)
   but legacy rows from before the trigger landed never had their vector
   computed. We re-trigger the BEFORE UPDATE path with a no-op
   ``content = content`` write so every NULL row gets re-tokenised.

The migration is idempotent: running it twice is a no-op; downgrade
removes the unique constraint and leaves data untouched (raw_content +
content_hash + search_vector are all preserved).

Revision: 0048
Down revision: 0047
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


_UNIQUE_INDEX_NAME = "uq_documents_bot_content_hash"


def upgrade() -> None:
    # ── 1. Backfill content_hash for any legacy rows still on the
    #       "pending" placeholder default (migration 0013). Re-runs are
    #       cheap because the WHERE filter is selective.
    op.execute(text("""
        UPDATE public.documents
        SET content_hash = encode(sha256(raw_content::bytea), 'hex')
        WHERE (content_hash IS NULL OR content_hash = 'pending')
          AND raw_content IS NOT NULL
    """))

    # ── 2. De-duplicate any pre-existing live duplicates BEFORE adding
    #       the unique index — soft-delete the older copies so the
    #       upgrade never fails on existing data. Audit trail is preserved
    #       (deleted_at = now(), rows still queryable).
    op.execute(text("""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY record_bot_id, content_hash
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM public.documents
            WHERE deleted_at IS NULL
              AND content_hash IS NOT NULL
        )
        UPDATE public.documents d
        SET deleted_at = now()
        FROM ranked r
        WHERE d.id = r.id
          AND r.rn > 1
    """))

    # ── 3. Partial UNIQUE constraint on live rows only. NULL deleted_at
    #       = live document; soft-deleted dupes are exempt so re-ingest
    #       after delete still works.
    op.execute(text(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE_INDEX_NAME}
        ON public.documents (record_bot_id, content_hash)
        WHERE deleted_at IS NULL
    """))

    # ── 4. BM25 search_vector backfill. Trigger is BEFORE INSERT OR
    #       UPDATE OF content, content_segmented (migration 0046) so a
    #       no-op write on ``content`` is the canonical way to recompute
    #       the tsvector for legacy rows. Idempotent.
    op.execute(text("""
        UPDATE public.document_chunks
        SET content = content
        WHERE search_vector IS NULL
    """))


def downgrade() -> None:
    # Drop only the unique index — backfilled hashes + search_vectors are
    # left in place because they are never wrong (recomputable from data).
    op.execute(text(f"""
        DROP INDEX IF EXISTS public.{_UNIQUE_INDEX_NAME}
    """))
