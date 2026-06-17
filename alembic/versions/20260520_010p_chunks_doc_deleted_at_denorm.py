"""[T2-CostPerf] Wave M3.5-C — denormalize doc_deleted_at into document_chunks.

Revision ID: 010p
Revises: 010o
Create Date: 2026-05-20

Wave M3.5-AB cut p95 -2.3s by enabling 3 default-OFF config flags. The
remaining gap to SLA (8s) is dominated by ``retrieve`` p50 1.6s — and
profiling (Sonnet audit Finding 5) traced ~80% of that cost to a
correlated EXISTS subquery in pgvector_store.py:226-230:

  WHERE record_bot_id = :bot
    AND EXISTS (SELECT 1 FROM documents d
                WHERE d.id = document_chunks.record_document_id
                  AND d.deleted_at IS NULL)
  ORDER BY embedding <=> :q LIMIT :k

The EXISTS forces Postgres to materialise document_chunks candidates
THEN filter against a per-row subquery — HNSW push-down stays active
for the WHERE clause but the soft-delete gate executes per-candidate.

This migration denormalises ``documents.deleted_at`` onto
``document_chunks.doc_deleted_at`` (TIMESTAMPTZ NULL), maintained by a
trigger on ``documents`` UPDATE/INSERT. The ANN query then becomes a
single-table scan:

  WHERE record_bot_id = :bot AND doc_deleted_at IS NULL
  ORDER BY embedding <=> :q LIMIT :k

A partial index on ``(record_bot_id) WHERE doc_deleted_at IS NULL``
keeps the index slim (most chunks are non-deleted).

Idempotent: re-running the migration on a DB that already has the
column / trigger / index is a no-op.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010p"
down_revision: str | None = "010o"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add doc_deleted_at column + trigger + partial index + backfill."""
    # 1. Column (NULL = doc is active; non-NULL = doc soft-deleted at that time).
    op.execute(
        text(
            """
            ALTER TABLE document_chunks
            ADD COLUMN IF NOT EXISTS doc_deleted_at TIMESTAMPTZ
            """
        )
    )

    # 2. Backfill existing rows from documents.deleted_at.
    op.execute(
        text(
            """
            UPDATE document_chunks dc
            SET doc_deleted_at = d.deleted_at
            FROM documents d
            WHERE dc.record_document_id = d.id
              AND dc.doc_deleted_at IS DISTINCT FROM d.deleted_at
            """
        )
    )

    # 3. Partial index over the hot ANN path — only active chunks per bot.
    #    Most chunks have doc_deleted_at IS NULL → index stays slim.
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_chunks_bot_active
            ON document_chunks (record_bot_id)
            WHERE doc_deleted_at IS NULL
            """
        )
    )

    # 4. Trigger to keep doc_deleted_at in sync with documents.deleted_at.
    #    Fires on UPDATE of documents.deleted_at; INSERT path covered by
    #    ingest service (DocumentService.ingest stamps NULL on new chunks).
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION sync_doc_deleted_at_to_chunks()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                    UPDATE document_chunks
                    SET doc_deleted_at = NEW.deleted_at
                    WHERE record_document_id = NEW.id;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )

    op.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_sync_doc_deleted_at ON documents;
            CREATE TRIGGER trg_sync_doc_deleted_at
            AFTER UPDATE OF deleted_at ON documents
            FOR EACH ROW
            EXECUTE FUNCTION sync_doc_deleted_at_to_chunks();
            """
        )
    )


def downgrade() -> None:
    """Drop trigger + index + column (back to JOIN-based filter)."""
    op.execute(text("DROP TRIGGER IF EXISTS trg_sync_doc_deleted_at ON documents"))
    op.execute(text("DROP FUNCTION IF EXISTS sync_doc_deleted_at_to_chunks()"))
    op.execute(text("DROP INDEX IF EXISTS ix_chunks_bot_active"))
    op.execute(
        text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS doc_deleted_at")
    )
