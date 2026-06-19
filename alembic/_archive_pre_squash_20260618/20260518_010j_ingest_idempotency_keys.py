"""[T2-CostPerf] ingest_idempotency_keys table — BE-to-BE replay safety

Revision ID: 010j
Revises: 010i
Create Date: 2026-05-18

Case-study P0-3 (upload flow audit `c524ba2`): when a partner BE
retries a ``POST /documents/...`` because the previous attempt timed
out at the gateway, the worker MUST NOT double-ingest. Without an
idempotency record the duplicate ingest creates orphan chunks +
duplicate FAQ entries that downstream retrieval surfaces twice.

Contract:

- Partner generates an opaque idempotency key per logical upload
  attempt and sends it on ``X-Idempotency-Key``.
- Service inserts a row keyed by ``(record_tenant_id, workspace_id,
  idempotency_key)``. First insert wins (``UniqueConstraint``);
  subsequent insert raises ``IntegrityError`` → service responds with
  the original ``document_id``.
- ``request_hash`` is the SHA-256 of the canonical request body so the
  service can detect "same key, different payload" abuse vs honest
  retry.
- ``expires_at`` defaults to ``now() + 24h`` via the service layer.
  A nightly sweep ``DELETE WHERE expires_at < now()`` keeps the table
  bounded.

4-key isolation: ``(record_tenant_id, workspace_id, idempotency_key)``
unique — same key across tenants stays distinct (no leak), same key
across workspaces inside one tenant also stays distinct.

RLS: same FORCE policy applied to ``audit_log`` / ``quotas`` — every
SELECT filters on ``record_tenant_id = current_setting('app.tenant_id')``
so cross-tenant probes return 0 rows even with a misbuilt query.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "010j"
down_revision = "010i"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_idempotency_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "record_document_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # State machine: "processing" | "done" | "failed". The service
        # writes "processing" on insert; the worker upgrades to "done"
        # once the document persists (or "failed" on a terminal error).
        # Stored as String(16) instead of an ENUM to keep schema-evolve
        # cheap (a new state = INSERT, not ALTER TYPE).
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "record_tenant_id",
            "workspace_id",
            "idempotency_key",
            name="uq_ingest_idemkey",
        ),
    )
    op.create_index(
        "ix_ingest_idemkey_expires",
        "ingest_idempotency_keys",
        ["expires_at"],
    )
    # FORCE row-level security — same posture as other tenant-scoped
    # tables. Each session sets ``app.tenant_id`` before SELECT/INSERT.
    op.execute(
        "ALTER TABLE ingest_idempotency_keys ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE ingest_idempotency_keys FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY ingest_idemkey_tenant_isolation
        ON ingest_idempotency_keys
        USING (
          record_tenant_id::text = current_setting('app.tenant_id', true)
        )
        WITH CHECK (
          record_tenant_id::text = current_setting('app.tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS ingest_idemkey_tenant_isolation "
        "ON ingest_idempotency_keys"
    )
    op.drop_index(
        "ix_ingest_idemkey_expires",
        table_name="ingest_idempotency_keys",
    )
    op.drop_table("ingest_idempotency_keys")
