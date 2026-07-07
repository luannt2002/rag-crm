"""Enable stats_brand_aware for bot chinh-sach-xe (ADR-0008 A2/B3 measurement).

Sacred #7: tracked alembic, never psql. Narrows the stats candidate set by the
query's discriminating tokens so "giá lốp Rovelo 195/55R16" is not served a
same-size row of a different brand (the size-code keyword alone is brand-blind).
Domain-neutral (candidate set = the dictionary). Default OFF platform-wide; opts
in the reference bot only, to measure the residual false-deny flip.

Reversible: downgrade removes the key.

Revision ID: stats_brand_csx_260707
Revises: stats_shape_csx_260707
"""
from __future__ import annotations

from alembic import op

revision = "stats_brand_csx_260707"
down_revision = "stats_shape_csx_260707"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = jsonb_set(
                COALESCE(plan_limits, '{}'::jsonb),
                '{stats_brand_aware}', 'true'::jsonb, true)
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bots
        SET plan_limits = plan_limits - 'stats_brand_aware'
        WHERE bot_id = 'chinh-sach-xe' AND channel_type = 'web'
        """
    )
