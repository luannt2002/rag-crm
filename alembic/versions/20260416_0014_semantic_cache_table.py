"""0014 — semantic_cache table (pgvector cosine + SHA256 exact match).

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.semantic_cache (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bot_id UUID NOT NULL,
            tenant_id UUID NULL,
            bot_version TEXT NOT NULL DEFAULT 'latest',
            corpus_version TEXT NOT NULL DEFAULT 'latest',
            query_embedding vector(1024),
            query_hash CHAR(64) NOT NULL,
            answer TEXT NOT NULL,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            model_name TEXT NOT NULL DEFAULT '',
            cached_at_ts BIGINT NOT NULL DEFAULT 0,
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NULL
        )
    """)

    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_sem_cache_bot ON {SCHEMA}.semantic_cache (bot_id, query_hash)"
    )
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_sem_cache_embedding_hnsw
        ON {SCHEMA}.semantic_cache USING hnsw (query_embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.semantic_cache CASCADE")
