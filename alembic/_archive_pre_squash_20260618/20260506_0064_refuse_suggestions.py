"""Stream H — Active learning SQL-only (refuse_suggestions).

Tracks frequently-refused query intents per bot so administrators can
discover knowledge gaps and improve bot coverage via document uploads or
system_prompt tuning.

Revision ID: 0064
Revises: 0063
Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refuse_suggestions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("record_tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("record_bot_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("query_intent", sa.String(64), nullable=False),
        sa.Column("refuse_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("sample_query", sa.Text, nullable=False, server_default=""),
    )
    op.create_index(
        "ix_refuse_suggestions_tenant_bot",
        "refuse_suggestions",
        ["record_tenant_id", "record_bot_id"],
    )
    op.create_unique_constraint(
        "uq_refuse_suggestions_bot_intent",
        "refuse_suggestions",
        ["record_bot_id", "query_intent"],
    )


def downgrade() -> None:
    op.drop_table("refuse_suggestions")
