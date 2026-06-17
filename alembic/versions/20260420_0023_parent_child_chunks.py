"""0023 — Add parent_chunk_id column for parent-child chunking (small-to-big retrieval).

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-20
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE document_chunks "
        "ADD COLUMN IF NOT EXISTS parent_chunk_id UUID "
        "REFERENCES document_chunks(id) ON DELETE SET NULL"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_chunks_parent "
        "ON document_chunks(parent_chunk_id) "
        "WHERE parent_chunk_id IS NOT NULL"
    ))

    # Seed parent-child config keys
    for key, value, value_type, description in [
        ("parent_child_enabled", "false", "bool", "Bật/tắt parent-child chunking (small-to-big retrieval)"),
        ("parent_chunk_size", "1024", "int", "Kích thước parent chunk (chars)"),
        ("child_chunk_size", "256", "int", "Kích thước child chunk (chars)"),
        ("child_chunk_overlap", "50", "int", "Overlap giữa child chunks (chars)"),
    ]:
        op.execute(text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:key, CAST(:val AS jsonb), :vtype, :desc, now())
            ON CONFLICT (key) DO NOTHING
        """).bindparams(key=key, val=value, vtype=value_type, desc=description))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS idx_chunks_parent"))
    op.execute(text(
        "ALTER TABLE document_chunks DROP COLUMN IF EXISTS parent_chunk_id"
    ))
    op.execute(text(
        "DELETE FROM system_config WHERE key = ANY(:keys)"
    ).bindparams(keys=[
        "parent_child_enabled",
        "parent_chunk_size",
        "child_chunk_size",
        "child_chunk_overlap",
    ]))
