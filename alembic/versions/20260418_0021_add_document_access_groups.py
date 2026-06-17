"""0021 — Add access_groups column to documents for permission pre-filtering.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-18
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add access_groups TEXT[] column with GIN index for permission filtering
    op.execute(text(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS access_groups TEXT[] DEFAULT '{}'"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_documents_access_groups "
        "ON documents USING GIN (access_groups)"
    ))

    # Seed permission-related system_config keys
    for key, value, value_type, description in [
        ("permission_filtering_enabled", "false", "bool", "Bật/tắt permission filtering khi retrieve"),
        ("permission_default_public", "true", "bool", "Docs không có access_groups được coi là public"),
    ]:
        op.execute(text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:key, CAST(:val AS jsonb), :vtype, :desc, now())
            ON CONFLICT (key) DO NOTHING
        """).bindparams(key=key, val=value, vtype=value_type, desc=description))


def downgrade() -> None:
    op.execute(text("ALTER TABLE documents DROP COLUMN IF EXISTS access_groups"))
    op.execute(text(
        "DELETE FROM system_config WHERE key = ANY(:keys)"
    ).bindparams(keys=["permission_filtering_enabled", "permission_default_public"]))
