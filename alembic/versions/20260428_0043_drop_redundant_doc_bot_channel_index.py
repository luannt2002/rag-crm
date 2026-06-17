"""S9 W-A0 phase 8c (B.4): drop the redundant ``ix_doc_bot_channel`` composite.

Why: ``record_bot_id`` is 1:1 with the external triple ``(tenant_id, bot_id,
channel_type)`` via the ``uq_bots_tenant_bot_channel`` UNIQUE constraint. So
indexing ``documents`` on ``(record_bot_id, channel_type)`` adds nothing to
selectivity — the first column already partitions every query. We only ever
filter by ``record_bot_id`` (Sprint 9 B.5 cleanup) and the second column
just costs storage + write amplification.

This migration replaces the composite with a single-column index on
``record_bot_id`` to keep the cheap lookup path that ``document_service``,
``pgvector_store``, and the orchestration retrieval node depend on.

We CANNOT use ``CREATE INDEX CONCURRENTLY`` here because alembic wraps
migrations in a transaction. For prod replays where downtime matters,
operators can run the equivalent ``DROP/CREATE INDEX CONCURRENTLY`` by
hand and stamp this revision; the SQL is small and idempotent.

Revision: 0043
Down revision: 0042
"""

from __future__ import annotations

from alembic import op


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop ``ix_doc_bot_channel``; create ``ix_doc_bot`` on record_bot_id only."""
    op.execute("DROP INDEX IF EXISTS ix_doc_bot_channel")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doc_bot ON documents (record_bot_id)"
    )


def downgrade() -> None:
    """Restore the legacy composite shape."""
    op.execute("DROP INDEX IF EXISTS ix_doc_bot")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doc_bot_channel "
        "ON documents (record_bot_id, channel_type)"
    )
