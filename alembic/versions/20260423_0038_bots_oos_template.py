"""P16 Wave 3 Phase 11: per-bot oos_answer_template.

Adds optional per-bot override for the out-of-scope / no-context
response. When NULL (default), the bot falls back to the i18n
language-pack default. Supports {hotline} and {bot_name} placeholders
rendered at query time.

Revision: 0038
Down revision: 0037
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE bots
        ADD COLUMN IF NOT EXISTS oos_answer_template VARCHAR(1000) NULL
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS oos_answer_template"))
