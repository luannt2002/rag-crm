"""Sprint 12A — drop documents.channel_type column.

Sprint 9 Wave A2 (gap B.5) removed all retrieval-time filters on
``channel_type`` because ``record_bot_id`` is 1:1 with the
``(tenant_id, bot_id, channel_type)`` external triple via
``uq_bots_tenant_bot_channel``. Migration 0043 already dropped the
composite index ``ix_doc_bot_channel`` for the same reason; the column
itself has been dead data ever since.

Revision: 0047
Down revision: 0046
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive: drop any leftover index that could reference the column
    # (composite index was dropped in 0043 — these are no-ops on a clean DB).
    op.execute(text("DROP INDEX IF EXISTS ix_doc_bot_channel"))
    op.execute(text("DROP INDEX IF EXISTS ix_documents_channel_type"))
    op.execute(text("ALTER TABLE documents DROP COLUMN IF EXISTS channel_type"))


def downgrade() -> None:
    # Re-add column with the same shape it had pre-0047 (added in 0032 with
    # NOT NULL DEFAULT 'web'). Repopulate from bots.channel_type via LEFT
    # JOIN + COALESCE so soft-deleted bots (deleted_at IS NOT NULL) — which
    # the row-level filter in production reads may exclude — still produce a
    # legal value rather than tripping the NOT NULL constraint.
    #
    # Caveat (documented, accepted): when a bot row is hard-deleted between
    # upgrade-0047 and downgrade-0047, the original ``channel_type`` cannot
    # be recovered and falls back to the column DEFAULT ``'web'``. This is
    # only relevant in dev rollback scenarios; production never deletes
    # bots, only soft-deletes them, so the JOIN should always match. We
    # log a NOTICE row-count for any documents that fell back to the
    # default so the operator can audit drift.
    op.execute(text(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
        "channel_type VARCHAR(32) NOT NULL DEFAULT 'web'"
    ))
    op.execute(text(
        "UPDATE documents d "
        "SET channel_type = COALESCE(b.channel_type, 'web') "
        "FROM bots b "
        "WHERE d.record_bot_id = b.id"
    ))
    # Audit: how many documents kept the DEFAULT because no bot row matched
    # (hard-delete case). Surfaced via psql NOTICE so the rollback operator
    # sees drift without us failing the migration.
    op.execute(text(
        "DO $$ "
        "DECLARE missing_count BIGINT; "
        "BEGIN "
        "  SELECT COUNT(*) INTO missing_count "
        "    FROM documents d "
        "    LEFT JOIN bots b ON b.id = d.record_bot_id "
        "    WHERE b.id IS NULL; "
        "  IF missing_count > 0 THEN "
        "    RAISE NOTICE '0047 downgrade: % documents lost original channel_type (no bot row); fell back to default web', missing_count; "
        "  END IF; "
        "END $$;"
    ))
