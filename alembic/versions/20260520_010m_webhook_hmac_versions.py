"""[T1-Smartness] Webhook HMAC secret rotation — versioned secrets table.

Revision ID: 010m
Revises: 010l
Create Date: 2026-05-20

Security mindset — case-study upload-flow P1-4: a webhook HMAC secret
hard-coded for the lifetime of the integration is a permanent leak if
compromised. Replace it with a versioned table that lets operators
``rotate-secret`` on demand and lets the verifier accept BOTH the
current and the previous secret during a configurable grace window
so partner integrations can roll their consumer without downtime.

Schema:

* ``tenant_webhooks`` — the parent registration (one row per webhook a
  tenant wires). Kept intentionally minimal here (id, record_tenant_id,
  url, created_at, revoked_at) — richer columns (description, event
  filters, ...) will be layered by the webhook-CRUD work-stream
  without touching this migration's contract.
* ``tenant_webhook_secrets`` — child rows holding ONE bcrypt hash per
  rotation generation. Plain secrets NEVER persist; the rotate endpoint
  returns the freshly-minted secret exactly once for the caller to
  record on their side. ``grace_period_hours`` is captured per row so
  the verifier can compute ``revoked_at + grace`` per-secret instead
  of relying on a global system_config knob.

Down-revision rationale: WA-3 (010l_chunk_context) ships in the same
Wave A but has not yet merged at WA-6's branch point. The mandate
explicitly says to point at ``010k`` and let the Auditor renumber to
``010m`` once both streams reach main. The string id ``"010m"`` stays
(per ship plan); the Auditor edits ``down_revision`` if 010l lands
first.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010m"
# Chain: 010k → 010l (chunk_context) → 010m (this) → 010n (BM25 idx).
# FK / schema here have no dependency on the chunk_context column.
down_revision = "010l"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Parent registration table. Held to the minimum columns the
    # secret-rotation feature needs so it does not pre-empt the
    # broader webhook-CRUD design (description, event mask, ...).
    op.create_table(
        "tenant_webhooks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_tenant_webhooks_tenant",
        "tenant_webhooks",
        ["record_tenant_id"],
    )

    op.create_table(
        "tenant_webhook_secrets",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "webhook_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant_webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        # bcrypt hash — never the plain secret. 128 covers bcrypt's
        # 60-char fixed width with headroom for future algo (argon2id
        # produces ~95 chars).
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        # Set to NOW() + grace at rotate-time. Verifier accepts the
        # secret while NOW() <= revoked_at; rejects past that point.
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Per-row grace so we can change the default without rewriting
        # historic rows; new rotations pick up the constant.
        sa.Column(
            "grace_period_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("24"),
        ),
    )
    op.create_index(
        "ix_tenant_webhook_secrets_lookup",
        "tenant_webhook_secrets",
        ["record_tenant_id", "webhook_id", "version"],
    )
    op.create_unique_constraint(
        "uq_tenant_webhook_secrets_version",
        "tenant_webhook_secrets",
        ["record_tenant_id", "webhook_id", "version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_tenant_webhook_secrets_version",
        "tenant_webhook_secrets",
        type_="unique",
    )
    op.drop_index(
        "ix_tenant_webhook_secrets_lookup",
        table_name="tenant_webhook_secrets",
    )
    op.drop_table("tenant_webhook_secrets")
    op.drop_index("ix_tenant_webhooks_tenant", table_name="tenant_webhooks")
    op.drop_table("tenant_webhooks")
