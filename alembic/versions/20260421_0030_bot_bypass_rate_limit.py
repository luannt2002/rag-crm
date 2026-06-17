"""Add bypass_rate_limit column to bots table.

When True, skip rate limiting for requests to this bot.
Use case: internal/load-test bots that need unlimited throughput.

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

SCHEMA = "public"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.bots
        ADD COLUMN IF NOT EXISTS bypass_rate_limit BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {SCHEMA}.bots
        DROP COLUMN IF EXISTS bypass_rate_limit
    """)
