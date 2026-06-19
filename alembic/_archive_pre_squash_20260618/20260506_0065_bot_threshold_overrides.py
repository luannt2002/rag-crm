"""Stream V Phase 2 — Per-bot threshold_overrides JSONB column.

Allows bot owners to override pipeline thresholds (reranker min score,
grounding check, context cap, etc.) at the bot level without touching
system_config or constants. The resolve chain is:

    bot column > plan_limits > system_config > schema default

Revision ID: 0065
Revises: 0064
Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column(
            "threshold_overrides",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bots", "threshold_overrides")
