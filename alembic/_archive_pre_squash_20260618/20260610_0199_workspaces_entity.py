"""Create workspaces entity — slug → first-class row (ADR-W2-D2).

Revision: 0199
Prev:     0198

Until now ``workspace`` existed only as a ``VARCHAR(64)`` slug on ``bots``
and 16 data tables (alembic 0062): good enough to carry IDENTITY and
DATA-scoping (RLS workspace GUC, 0141), but it cannot anchor an RBAC or
quota boundary, nor a lifecycle (create / soft-delete / offboard).

This adds the entity BESIDE the slug — it does NOT change the 4-key
identity tuple (``record_tenant_id, workspace_id, bot_id, channel_type``)
and does NOT add a FK NOT-NULL on ``bots`` (which would gate the write
path). ``bots.workspace_id`` stays the canonical slug; ``workspaces`` is an
additive reference for RBAC / quota / lifecycle (ADR-W2-D2 §a, §4 rejects
adding ``record_workspace_id`` to the tuple).

Backfill is no-downtime: one row per distinct ``(record_tenant_id,
workspace_id)`` already present on ``bots``. Because 0062 defaulted
``bots.workspace_id = record_tenant_id::text``, every tenant gets at least
its implicit "default" workspace (slug == tenant UUID) — the null→default
workspace contract.

RLS: ENABLE + FORCE, scoped on ``record_tenant_id`` only (the entity is
tenant-scoped; the slug is its payload, not a second isolation axis on this
table). Matches the 0141 / 0187 fail-closed pattern and the ``app.tenant_id``
GUC wired in ADR-W1-D3.

Sacred-rule alignment:
  - Pure DDL via alembic (no psql hot-fix)
  - Reversible — downgrade drops policy + table
  - Domain-neutral — generic tenancy table; slug is tenant-defined
  - 4-key identity untouched (additive entity)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0199"
down_revision: str | None = "0198"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "workspaces"
_POLICY = "rls_workspaces_tenant"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id", UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_tenant_id", UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "record_tenant_id", "slug", name="uq_workspaces_tenant_slug",
        ),
    )
    # Tenant-lead index (RLS predicate + list-by-tenant), live rows only.
    op.execute(
        "CREATE INDEX ix_workspaces_tenant ON workspaces (record_tenant_id) "
        "WHERE deleted_at IS NULL",
    )

    # Backfill one workspace per distinct (tenant, slug) already on bots.
    # name defaults to the slug; owners can rename via the control plane.
    op.execute(
        """
        INSERT INTO workspaces (record_tenant_id, slug, name)
        SELECT DISTINCT record_tenant_id, workspace_id, workspace_id
        FROM bots
        WHERE workspace_id IS NOT NULL
        ON CONFLICT (record_tenant_id, slug) DO NOTHING
        """,
    )

    # RLS — tenant-scoped, fail-closed (admin sessions with no GUC see 0
    # rows, not an error — the ``true`` second arg to current_setting).
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON {_TABLE}
        FOR ALL
        USING (
            record_tenant_id = current_setting('app.tenant_id', true)::uuid
        )
        WITH CHECK (
            record_tenant_id = current_setting('app.tenant_id', true)::uuid
        )
        """,
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(_TABLE)
