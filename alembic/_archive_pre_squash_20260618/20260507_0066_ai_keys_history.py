"""ai_keys table for hot key rotation history (Stream J).

Per-provider multi-key history with status (active / rotated_out / burned /
verifying / archived). Supersedes single-key in ai_providers.metadata
(which stays for backward compat — first migration leaves existing keys
in place; new rotations write to ai_keys).

Revision ID: 0066
Revises: 0065
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_keys",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("api_key_encrypted", sa.Text, nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "is_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_status", sa.String(32), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_by_user_id", sa.String(64), nullable=True),
        sa.Column(
            "metadata_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_ai_keys_provider_active",
        "ai_keys",
        ["record_provider_id", "status"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_ai_keys_provider_default",
        "ai_keys",
        ["record_provider_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_keys_provider_default", table_name="ai_keys")
    op.drop_index("ix_ai_keys_provider_active", table_name="ai_keys")
    op.drop_table("ai_keys")
