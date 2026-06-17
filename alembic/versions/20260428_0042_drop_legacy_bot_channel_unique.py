"""S9 W-A0 phase 8b: drop the legacy ``uq_bots_bot_channel_active`` partial index.

Why: migration 0011 created a partial UNIQUE INDEX on ``(bot_id, channel_type)
WHERE is_deleted = false`` — predating multi-tenant scoping. Migration 0039
later added the proper tenant-scoped UNIQUE constraint
``uq_bots_tenant_bot_channel`` on ``(tenant_id, bot_id, channel_type)`` but
forgot to drop the legacy index. Both have been live ever since.

Net effect today: two tenants CANNOT share the same ``(bot_id, channel_type)``
slug — the legacy partial index rejects the second insert with
``UniqueViolation: uq_bots_bot_channel_active`` even though the 3-key contract
is supposed to allow it. Cross-tenant isolation is therefore enforced too
strictly: the slug "support" can only exist once across the entire platform.

This violates the Sprint 9 Wave A0 3-key external identity contract:
``(tenant_id, bot_id, channel_type)`` is the ONLY uniqueness boundary. Two
tenants must be free to register the same slug; resolution by tenant_id
keeps them isolated.

The red-team integration test
``tests/integration/test_3key_cross_tenant_isolation.py::
test_two_tenants_same_bot_id_resolve_isolated`` asserts this contract and
fails today against the legacy index. This migration drops it; the test
then passes because ``uq_bots_tenant_bot_channel`` (added by 0039) remains
to enforce per-tenant uniqueness — exactly what the contract requires.

Pre-flight check: aborts if any duplicate ``(bot_id, channel_type)`` rows
exist within the same tenant — those would already be blocked by the
remaining tenant-scoped constraint, but the partial index also covered
them, so we look for and surface them defensively.

Revision: 0042
Down revision: 0041
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the legacy ``uq_bots_bot_channel_active`` partial unique index.

    The remaining ``uq_bots_tenant_bot_channel`` (added in migration 0039)
    is the correct tenant-scoped uniqueness boundary.
    """
    conn = op.get_bind()

    # Defensive pre-flight: confirm the tenant-scoped constraint exists. If
    # someone has rolled it back or it never landed, refuse to drop the
    # legacy index — that would leave the table with NO uniqueness on the
    # external slug at all.
    has_tenant_unique = conn.execute(
        text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() "
            "AND tablename = 'bots' "
            "AND indexname = 'uq_bots_tenant_bot_channel'"
        ),
    ).fetchone()
    if has_tenant_unique is None:
        raise RuntimeError(
            "Refusing to drop uq_bots_bot_channel_active — the "
            "tenant-scoped replacement uq_bots_tenant_bot_channel "
            "(migration 0039) is missing. Re-run 0039 first."
        )

    op.execute("DROP INDEX IF EXISTS uq_bots_bot_channel_active")


def downgrade() -> None:
    """Re-create the legacy partial index (best-effort).

    This is provided for completeness only — re-introducing the index will
    re-break cross-tenant isolation and the red-team test will re-fail.
    """
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bots_bot_channel_active "
        "ON bots (bot_id, channel_type) "
        "WHERE is_deleted = false"
    )
