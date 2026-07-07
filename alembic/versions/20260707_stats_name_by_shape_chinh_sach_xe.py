"""Enable stats_name_by_shape for bot chinh-sach-xe (ADR-0008 A1 measurement).

Sacred #7: bot config-state via a TRACKED alembic migration, never psql. Turns on
the shape-picked descriptive name for the stats synthetic chunk on the reference
bot so the served row carries the real product name ("Lốp Rovelo 195/55R16 …")
instead of the internal code ("2-R16 195/55 LPD"). Deterministic, zero-model,
zero-vocab (shared/table_shape.pick_descriptive_name). Default is observe/off
platform-wide; this opts IN the reference bot only, to measure the flip.

Reversible: downgrade removes the key.

Revision ID: stats_shape_csx_260707
Revises: brand_scope_csx_260707
"""
from __future__ import annotations

from alembic import op

revision = "stats_shape_csx_260707"
down_revision = "brand_scope_csx_260707"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                COALESCE(plan_limits, '{}'::jsonb),
                '{stats_name_by_shape}', 'true'::jsonb, true)
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = plan_limits - 'stats_name_by_shape'
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )
