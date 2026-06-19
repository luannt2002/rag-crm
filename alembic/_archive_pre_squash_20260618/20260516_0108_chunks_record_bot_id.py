"""[T1-Smartness] denormalize record_bot_id to document_chunks (MEGA-1 HNSW activate)

Revision ID: 0108
Revises: 0107b
Create Date: 2026-05-16

Live evidence (MEGA-1, ``reports/AUDIT_20260515/DBA_AUDIT_7TRUC.md``):
``ix_chunks_embedding_hnsw`` reports ``idx_scan = 0`` over a 22 MB index.
Root cause: ``document_chunks`` has NO ``record_bot_id`` column. The
retrieve query must JOIN ``documents`` to filter by bot, which prevents
the PostgreSQL planner from pushing the bot-isolation predicate INTO the
HNSW scan operator. The planner therefore degrades to brute-force cosine
over the full table, ignoring the index.

Fix: denormalize ``record_bot_id`` from ``documents`` onto
``document_chunks`` so the bot filter sits directly on the same relation
as the HNSW index — the planner can then push it down and the index
activates.

Also tightens RLS: tenant isolation policy on ``document_chunks`` now
joins via the local ``record_bot_id`` instead of an EXISTS over
``documents`` (faster + denormalized).

Indexes added (besides the column):
- ``ix_chunks_bot`` on ``(record_bot_id)`` — direct lookup
- ``ix_chunks_bot_doc`` on ``(record_bot_id, record_document_id)`` —
  composite for chunk-by-document scans within a bot

FK cascade: ``ON DELETE CASCADE`` mirrors the existing
``record_document_id → documents.id`` cascade — deleting a bot wipes
its chunks atomically.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0108"
down_revision = "0107b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable first so the back-fill UPDATE has somewhere to write.
    op.execute(text("ALTER TABLE document_chunks ADD COLUMN record_bot_id UUID"))
    op.execute(
        text(
            """
            UPDATE document_chunks dc
               SET record_bot_id = d.record_bot_id
              FROM documents d
             WHERE dc.record_document_id = d.id
            """
        )
    )
    # Promote to NOT NULL once back-fill completes — any orphan chunk
    # (record_document_id with no matching documents row) would surface
    # here as a constraint violation, which is the correct loud signal.
    op.execute(
        text("ALTER TABLE document_chunks ALTER COLUMN record_bot_id SET NOT NULL"),
    )
    op.execute(
        text(
            """
            ALTER TABLE document_chunks
            ADD CONSTRAINT fk_chunks_bot
            FOREIGN KEY (record_bot_id) REFERENCES bots(id) ON DELETE CASCADE
            """
        )
    )
    op.execute(text("CREATE INDEX ix_chunks_bot ON document_chunks(record_bot_id)"))
    op.execute(
        text(
            """
            CREATE INDEX ix_chunks_bot_doc
                ON document_chunks(record_bot_id, record_document_id)
            """
        )
    )
    # RLS policy refactor — replace the indirect ``record_document_id →
    # documents.record_bot_id → bots.record_tenant_id`` chain with a
    # direct ``record_bot_id → bots.record_tenant_id`` lookup that uses
    # the new index.
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON document_chunks"))
    op.execute(
        text(
            """
            CREATE POLICY tenant_isolation ON document_chunks
            FOR ALL TO ragbot_app
            USING (
                record_bot_id IN (
                    SELECT id FROM bots
                     WHERE record_tenant_id::text
                         = current_setting('app.tenant_id', true)
                )
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON document_chunks"))
    op.execute(text("DROP INDEX IF EXISTS ix_chunks_bot_doc"))
    op.execute(text("DROP INDEX IF EXISTS ix_chunks_bot"))
    op.execute(
        text(
            "ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS fk_chunks_bot",
        ),
    )
    op.execute(text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS record_bot_id"))
