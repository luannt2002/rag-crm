"""Add parallel column ``embedding_v3 vector(1024)`` for Jina v3 migration.

Strategy = parallel column: zero downtime; per-bot binding picks which column
to read at query time. Rollback = downgrade drops the new column + index.

Revision ID: 0054
Revises: 0053
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


# DDL identifiers (table/column names) — explicitly whitelisted vs zero-hardcode.
_DDL = (
    "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_v3 vector(1024)",
    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_v3_hnsw "
    "ON document_chunks USING hnsw (embedding_v3 vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64)",
    "ALTER TABLE semantic_cache ADD COLUMN IF NOT EXISTS query_embedding_v3 vector(1024)",
    "CREATE INDEX IF NOT EXISTS ix_sem_cache_embedding_v3_hnsw "
    "ON semantic_cache USING hnsw (query_embedding_v3 vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64)",
)

_DDL_DOWN = (
    "DROP INDEX IF EXISTS ix_sem_cache_embedding_v3_hnsw",
    "ALTER TABLE semantic_cache DROP COLUMN IF EXISTS query_embedding_v3",
    "DROP INDEX IF EXISTS ix_chunks_embedding_v3_hnsw",
    "ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_v3",
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("SET LOCAL statement_timeout = 0"))
    for stmt in _DDL:
        conn.execute(sa.text(stmt))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("SET LOCAL statement_timeout = 0"))
    for stmt in _DDL_DOWN:
        conn.execute(sa.text(stmt))
