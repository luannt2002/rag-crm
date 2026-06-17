"""P24-L2: bot slug uniqueness scoped per tenant.

Before: `ix_bots_bot_channel` was only an Index on (bot_id, channel_type).
Tenant A and Tenant B could both create bot_id="support", channel_type="web"
— the composite was not unique across tenants, so `find_by_bot_channel`
may return rows from the wrong tenant when upstream callers leak slugs.

After: replace the index with a UniqueConstraint on
(tenant_id, bot_id, channel_type). Pre-flight check aborts if duplicates
already exist — we can't auto-resolve without human input, so an operator
must pick which row to keep before the migration will run.

Revision: 0039
Down revision: 0038
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Promote index to a tenant-scoped UniqueConstraint."""
    conn = op.get_bind()
    dups = conn.execute(
        text(
            "SELECT tenant_id, bot_id, channel_type, COUNT(*) AS c "
            "FROM bots "
            "WHERE deleted_at IS NULL "
            "GROUP BY tenant_id, bot_id, channel_type "
            "HAVING COUNT(*) > 1"
        ),
    ).fetchall()
    if dups:
        raise RuntimeError(
            f"Cannot add uniqueness — {len(dups)} duplicate bot slugs "
            f"exist: {[tuple(r) for r in dups[:5]]}. Resolve manually first."
        )

    op.drop_index("ix_bots_bot_channel", table_name="bots")
    op.create_unique_constraint(
        "uq_bots_tenant_bot_channel",
        "bots",
        ["tenant_id", "bot_id", "channel_type"],
    )


def downgrade() -> None:
    """Roll back to the old non-unique index."""
    op.drop_constraint("uq_bots_tenant_bot_channel", "bots", type_="unique")
    op.create_index("ix_bots_bot_channel", "bots", ["bot_id", "channel_type"])
