"""S9 W-A0: tighten bots.tenant_id to NOT NULL.

Why: `tenant_id` is the multi-tenancy scope key. While 0039 added a
UniqueConstraint on (tenant_id, bot_id, channel_type), nothing yet prevents
tenant_id itself from being NULL. A NULL tenant_id means a bot can sit outside
any tenant scope, which (a) silently bypasses tenant-scoped queries and
(b) lets two NULL-tenant rows coexist with the same (bot_id, channel_type)
because PostgreSQL treats NULL as distinct in unique constraints — that is a
cross-tenant identity collision waiting to happen.

This migration closes that gap by promoting tenant_id to NOT NULL. A pre-flight
check aborts if any NULL rows exist; the operator must backfill or delete them
before re-running.

Revision: 0041
Down revision: 0040
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Promote bots.tenant_id from NULLABLE to NOT NULL."""
    conn = op.get_bind()
    null_count = conn.execute(
        text("SELECT COUNT(*) FROM bots WHERE tenant_id IS NULL"),
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            f"Cannot tighten bots.tenant_id to NOT NULL — {null_count} row(s) "
            "still have tenant_id IS NULL. Backfill or delete those rows "
            "before re-running this migration."
        )

    op.alter_column(
        "bots",
        "tenant_id",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    """Relax bots.tenant_id back to NULLABLE."""
    op.alter_column(
        "bots",
        "tenant_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
