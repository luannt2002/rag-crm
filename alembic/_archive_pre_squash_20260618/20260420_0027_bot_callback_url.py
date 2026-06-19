"""0027 — Add callback_url column to bots table.

Per-bot callback URL (nullable). Resolution order:
  request callback_url > bot callback_url > tenant callback_url > None (poll only)
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE bots ADD COLUMN IF NOT EXISTS callback_url TEXT NULL;
        COMMENT ON COLUMN bots.callback_url IS 'Default callback URL for this bot. NULL = use tenant/request callback';
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE bots DROP COLUMN IF EXISTS callback_url;
    """))
