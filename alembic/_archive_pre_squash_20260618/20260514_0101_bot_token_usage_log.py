"""[T2-CostPerf] bot_token_usage_log — per-month token accounting per bot (4-key identity)

Revision ID: 0101
Revises: 0100
Create Date: 2026-05-14

Token Quota Monetization — historical per-month usage log for billing,
analytics and quota-reset window calculation.

One row per ``(record_tenant_id, workspace_id, bot_id, channel_type)``
tuple — matches the platform 4-key bot identity rule (see CLAUDE.md
IDENTITY RULE). ``usage_by_month`` is a JSONB map keyed by
``"YYYY_MM"`` so a single row holds the bot's full lifetime accounting
without a row explosion (12 keys/year vs 12 rows/year).

``record_bot_id`` UUID denormalises the resolved internal PK from
``bots.id`` — accountant writes use it for an O(1) join on the hot
write-path (avoid 4-column composite re-resolve every increment).

Indexes:
- ``ix_bot_token_usage_log_record_bot`` — primary lookup by internal id
  on the chat write path.
- ``ix_bot_token_usage_log_tenant`` — admin "tenant rollup" queries that
  scan all bots inside a tenant.

UNIQUE on the 4-key tuple enforces "one accounting row per bot" — the
accountant uses ``ON CONFLICT (... 4-key ...) DO UPDATE`` to mutate the
JSONB month bucket idempotently.

Downgrade drops the entire table (including all indexes and the unique
constraint) — accounting history is acceptable to lose on rollback in
exchange for a clean revert path.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_token_usage_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("record_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.String(length=255), nullable=False),
        sa.Column("channel_type", sa.String(length=64), nullable=False),
        sa.Column("record_bot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "usage_by_month",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "record_tenant_id",
            "workspace_id",
            "bot_id",
            "channel_type",
            name="uq_bot_token_usage_log_4key",
        ),
    )

    op.create_index(
        "ix_bot_token_usage_log_record_bot",
        "bot_token_usage_log",
        ["record_bot_id"],
    )
    op.create_index(
        "ix_bot_token_usage_log_tenant",
        "bot_token_usage_log",
        ["record_tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_token_usage_log_tenant", table_name="bot_token_usage_log"
    )
    op.drop_index(
        "ix_bot_token_usage_log_record_bot", table_name="bot_token_usage_log"
    )
    op.drop_table("bot_token_usage_log")
