"""[T1-Smartness] fix semantic_cache.query_embedding dim 1024 -> 1280

Revision ID: 0105
Revises: 0104
Create Date: 2026-05-16

Why
---
Live evidence (mega-sprint audit, 2026-05-15):
- ZeroEntropy ``zembed-1`` returns 1280-dim matryoshka vectors (post commit
  ``b9e7761``).
- ``semantic_cache.query_embedding`` column was provisioned ``vector(1024)``
  in alembic 0063 (Jina v3 carry-over).
- Every ``INSERT`` raises ``DataError: wrong number of dimensions``; the
  cache writer wraps the call in a broad ``except Exception`` so the error
  is silently swallowed and the cache write is dropped.
- Net effect: F6 — semantic cache hit rate is **0%** in production for
  ZeroEntropy bots, with no visible failure signal.

Pre-condition (verified 2026-05-15):
- ``SELECT count(*) FROM semantic_cache;`` returns 0 — the table has
  never been written successfully on this DB. The DROP COLUMN +
  ADD COLUMN reconstruction is loss-less.

Idempotent: ``DROP INDEX IF EXISTS`` and ``DROP COLUMN IF EXISTS`` make
the upgrade safe to retry. Reversible via ``downgrade`` which restores
the legacy ``vector(1024)`` shape.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


_UPGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_semantic_cache_qe_hnsw",
    "ALTER TABLE semantic_cache DROP COLUMN IF EXISTS query_embedding",
    "ALTER TABLE semantic_cache ADD COLUMN query_embedding vector(1280)",
    (
        "CREATE INDEX ix_semantic_cache_qe_hnsw "
        "ON semantic_cache USING hnsw (query_embedding vector_cosine_ops) "
        "WITH (m = 32, ef_construction = 200)"
    ),
)


_DOWNGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS ix_semantic_cache_qe_hnsw",
    "ALTER TABLE semantic_cache DROP COLUMN IF EXISTS query_embedding",
    "ALTER TABLE semantic_cache ADD COLUMN query_embedding vector(1024)",
    (
        "CREATE INDEX ix_semantic_cache_qe_hnsw "
        "ON semantic_cache USING hnsw (query_embedding vector_cosine_ops) "
        "WITH (m = 32, ef_construction = 200)"
    ),
)


def upgrade() -> None:
    for stmt in _UPGRADE_SQL:
        op.execute(text(stmt))


def downgrade() -> None:
    for stmt in _DOWNGRADE_SQL:
        op.execute(text(stmt))
