"""Add chat_histories.served_chunks — persist the chunk list served to the LLM
per assistant turn (truth-audit verification requirement: every stored answer
must be auditable against exactly what the model saw, without debug mode).

Nullable JSONB — zero behavior change for existing rows; only assistant rows
written after this migration carry the list (capped items/chars, see
shared/constants SERVED_CHUNKS_*).

Revision ID: served_chunks_260703
Revises: seed_anti_pad_list_260701
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "served_chunks_260703"
down_revision = "seed_anti_pad_list_260701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_histories",
        sa.Column("served_chunks", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_histories", "served_chunks")
