"""Tenant UUID lift — bots.tenant_id INT → record_tenant_id UUID FK tenants(id).

Closes the schema drift where ``bots.tenant_id`` was the only INT tenant
reference while every other table used ``record_tenant_id UUID``. Aligns
``bots`` with platform-internal naming convention (``record_*_id`` =
UUID FK to a model PK).

Caller-facing impact:
- External request body NO LONGER carries ``tenant_id``. JWT bearer
  carries ``record_tenant_id`` UUID claim; middleware lifts onto
  ``request.state``.
- 3-key bot identity REMAINS ``(record_tenant_id UUID, bot_id, channel_type)``
  — the int → UUID swap preserves the triple semantically.

Idempotent: re-runs on already-migrated DB are no-op (column-existence
check at top).

Revision ID: 0058
Revises: 0057
Create Date: 2026-05-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    res = op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return res is not None


def upgrade() -> None:
    if _column_exists("bots", "record_tenant_id"):
        # Already migrated; no-op.
        return

    op.execute(
        """
        -- Add the UUID column nullable first to allow safe backfill.
        ALTER TABLE bots ADD COLUMN record_tenant_id UUID NULL
            REFERENCES tenants(id) ON DELETE RESTRICT;
        """
    )

    # Backfill from tenants.config->>'upstream_tenant_id' map. Bootstrap
    # script (scripts/db/seed_tenants_from_bots.py) must have run first to
    # populate tenants table; otherwise this migration fails noisily on
    # the NOT NULL flip below.
    op.execute(
        """
        UPDATE bots b
        SET record_tenant_id = t.id
        FROM tenants t
        WHERE (t.config->>'upstream_tenant_id')::int = b.tenant_id
          AND b.record_tenant_id IS NULL;
        """
    )

    # Verify zero unmapped rows before the destructive step.
    unmapped = op.get_bind().execute(
        text("SELECT COUNT(*) FROM bots WHERE record_tenant_id IS NULL")
    ).scalar()
    if unmapped:
        raise RuntimeError(
            f"alembic 0058 cannot proceed: {unmapped} bots row(s) have no "
            "matching tenant in tenants.config->>'upstream_tenant_id'. "
            "Run scripts/db/seed_tenants_from_bots.py first."
        )

    op.execute(
        """
        -- Lock down the new column.
        ALTER TABLE bots ALTER COLUMN record_tenant_id SET NOT NULL;

        -- Drop the legacy unique constraint that referenced INT tenant_id.
        ALTER TABLE bots DROP CONSTRAINT IF EXISTS uq_bots_tenant_bot_channel;

        -- New unique constraint on the UUID-based 3-key.
        ALTER TABLE bots ADD CONSTRAINT uq_bots_record_tenant_bot_channel
            UNIQUE (record_tenant_id, bot_id, channel_type);

        -- Replace lookup index.
        DROP INDEX IF EXISTS ix_bots_tenant_bot_channel;
        CREATE INDEX IF NOT EXISTS ix_bots_record_tenant_bot_channel
            ON bots (record_tenant_id, bot_id, channel_type);

        -- Drop the legacy INT column. Upstream tenant_id (if needed by
        -- callers) is recoverable via tenants.config->>'upstream_tenant_id'.
        ALTER TABLE bots DROP COLUMN tenant_id;
        """
    )


def downgrade() -> None:
    if not _column_exists("bots", "record_tenant_id"):
        return

    op.execute(
        """
        -- Re-add the legacy INT column nullable for backfill.
        ALTER TABLE bots ADD COLUMN tenant_id INTEGER NULL;

        UPDATE bots b
        SET tenant_id = (t.config->>'upstream_tenant_id')::int
        FROM tenants t
        WHERE b.record_tenant_id = t.id
          AND b.tenant_id IS NULL;

        ALTER TABLE bots ALTER COLUMN tenant_id SET NOT NULL;

        DROP INDEX IF EXISTS ix_bots_record_tenant_bot_channel;
        ALTER TABLE bots DROP CONSTRAINT IF EXISTS uq_bots_record_tenant_bot_channel;

        ALTER TABLE bots ADD CONSTRAINT uq_bots_tenant_bot_channel
            UNIQUE (tenant_id, bot_id, channel_type);
        CREATE INDEX IF NOT EXISTS ix_bots_tenant_bot_channel
            ON bots (tenant_id, bot_id, channel_type);

        ALTER TABLE bots DROP COLUMN record_tenant_id;
        """
    )
