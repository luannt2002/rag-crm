"""Add bypass_token_limit column to bots table.

When True, skip token budget validation for this bot.
Use case: internal/demo bots, or bots with external billing.

Revision ID: 0029
Revises: 0028
"""

from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

SCHEMA = "public"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.bots
        ADD COLUMN IF NOT EXISTS bypass_token_limit BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.bots
        DROP COLUMN IF EXISTS bypass_token_limit
    """)
