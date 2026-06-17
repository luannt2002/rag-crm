"""0015 — composite indexes for request_logs, bots, documents.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # request_logs: audit queries with date filter
    op.create_index(
        "ix_request_logs_tenant_started_status",
        "request_logs",
        ["tenant_id", text("started_at DESC"), "status"],
        if_not_exists=True,
        postgresql_concurrently=True,
    )

    # request_logs: bot-specific audit
    op.create_index(
        "ix_request_logs_bot_started",
        "request_logs",
        ["bot_id", text("started_at DESC")],
        if_not_exists=True,
        postgresql_concurrently=True,
    )

    # bots: partial index for active bot lookup
    op.create_index(
        "ix_bots_bot_channel_active",
        "bots",
        ["bot_id", "channel_type"],
        if_not_exists=True,
        postgresql_concurrently=True,
        postgresql_where=text("is_deleted = false"),
    )

    # documents: document listing per bot
    op.create_index(
        "ix_documents_bot_deleted",
        "documents",
        ["bot_id", "deleted_at"],
        if_not_exists=True,
        postgresql_concurrently=True,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_bot_deleted", table_name="documents", if_exists=True)
    op.drop_index("ix_bots_bot_channel_active", table_name="bots", if_exists=True)
    op.drop_index("ix_request_logs_bot_started", table_name="request_logs", if_exists=True)
    op.drop_index("ix_request_logs_tenant_started_status", table_name="request_logs", if_exists=True)
