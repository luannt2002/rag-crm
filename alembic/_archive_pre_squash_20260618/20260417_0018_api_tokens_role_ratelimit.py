"""0018 — api_tokens: add role + rate_limit_rps columns.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE api_tokens
        ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'service'
    """))
    # rate_limit_value: số request cho phép (0 = không giới hạn)
    op.execute(text("""
        ALTER TABLE api_tokens
        ADD COLUMN IF NOT EXISTS rate_limit_value INTEGER NOT NULL DEFAULT 120
    """))
    # rate_limit_window: khoảng thời gian tính bằng giây (vd: 60 = 1 phút)
    op.execute(text("""
        ALTER TABLE api_tokens
        ADD COLUMN IF NOT EXISTS rate_limit_window INTEGER NOT NULL DEFAULT 60
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE api_tokens DROP COLUMN IF EXISTS rate_limit_window"))
    op.execute(text("ALTER TABLE api_tokens DROP COLUMN IF EXISTS rate_limit_value"))
    op.execute(text("ALTER TABLE api_tokens DROP COLUMN IF EXISTS role"))
