"""0025 — Add max_history column to bots table.

Per-bot override for chat history message limit.
NULL = use system default (chat_max_history from system_config).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE bots ADD COLUMN IF NOT EXISTS max_history INT DEFAULT NULL;
        COMMENT ON COLUMN bots.max_history IS 'Max chat history messages per room. NULL = use system default';
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS max_history"))
