"""Consolidate embedding storage to a single column per table.

Result schema (one column per table — name reflects PURPOSE, not version):
- ``document_chunks.embedding``
- ``semantic_cache.query_embedding``

The runtime dimension is lifted from ``EmbeddingSpec`` per bot at call
time, not encoded in column names. Future provider/version swaps do not
require schema changes.

Pre-condition (verified before applying):
- The legacy parallel column has 0 populated rows on both tables — the
  drop is loss-less.

Revision ID: 0063
Revises: 0062
Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


_UPGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_chunks_embedding_hnsw",
    "ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding",
    "ALTER TABLE document_chunks RENAME COLUMN embedding_v3 TO embedding",
    "ALTER INDEX IF EXISTS ix_chunks_embedding_v3_hnsw RENAME TO ix_chunks_embedding_hnsw",

    "DROP INDEX IF EXISTS ix_sem_cache_embedding_hnsw",
    "ALTER TABLE semantic_cache DROP COLUMN IF EXISTS query_embedding",
    "ALTER TABLE semantic_cache RENAME COLUMN query_embedding_v3 TO query_embedding",
    "ALTER INDEX IF EXISTS ix_sem_cache_embedding_v3_hnsw RENAME TO ix_sem_cache_embedding_hnsw",
)

_DOWNGRADE_SQL: tuple[str, ...] = (
    "ALTER INDEX IF EXISTS ix_chunks_embedding_hnsw RENAME TO ix_chunks_embedding_v3_hnsw",
    "ALTER TABLE document_chunks RENAME COLUMN embedding TO embedding_v3",
    "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536)",
    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
    "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
    "WITH (m=16, ef_construction=64)",

    "ALTER INDEX IF EXISTS ix_sem_cache_embedding_hnsw RENAME TO ix_sem_cache_embedding_v3_hnsw",
    "ALTER TABLE semantic_cache RENAME COLUMN query_embedding TO query_embedding_v3",
    "ALTER TABLE semantic_cache ADD COLUMN IF NOT EXISTS query_embedding vector(1536)",
    "CREATE INDEX IF NOT EXISTS ix_sem_cache_embedding_hnsw "
    "ON semantic_cache USING hnsw (query_embedding vector_cosine_ops) "
    "WITH (m=16, ef_construction=64)",
)


def upgrade() -> None:
    for stmt in _UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE_SQL:
        op.execute(stmt)
