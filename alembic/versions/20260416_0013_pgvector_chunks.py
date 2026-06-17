"""0013 — pgvector extension + document_chunks table (thay Qdrant).

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.document_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL,
            bot_id UUID NOT NULL,
            tenant_id UUID NULL,
            chunk_index INT NOT NULL,
            content TEXT NOT NULL,
            content_hash CHAR(64) NOT NULL,
            embedding vector(1024),
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # FK document_id → documents if both in ragbot schema
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_chunks_document')
            THEN
                ALTER TABLE {SCHEMA}.document_chunks
                ADD CONSTRAINT fk_chunks_document
                FOREIGN KEY (document_id) REFERENCES {SCHEMA}.documents(id)
                ON DELETE CASCADE;
            END IF;
        END $$;
    """)

    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunks_document ON {SCHEMA}.document_chunks (document_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunks_bot ON {SCHEMA}.document_chunks (bot_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunks_content_hash ON {SCHEMA}.document_chunks (content_hash)"
    )
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
        ON {SCHEMA}.document_chunks USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.document_chunks CASCADE")
    # Keep vector extension — might be used elsewhere
