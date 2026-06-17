"""Fix semantic_cache.query_embedding dimension 1280 → 1024 (ZE→Jina leftover).

The ZE→Jina migration (0228) lifted document_chunks.embedding from
vector(1280) (ZeroEntropy zembed-1) to vector(1024) (jina-embeddings-v3) but
MISSED semantic_cache.query_embedding, which stayed vector(1280). Every
background cache write then failed at runtime:

    asyncpg.exceptions.DataError: expected 1280 dimensions, not 1024
    INSERT INTO semantic_cache (query_embedding ...)

so the semantic cache never populated (degraded latency + log spam). The cache
is ephemeral (TTL'd, 0 rows at migration time), so the column is rebuilt to the
canonical embedding dimension and the HNSW index recreated identically.

Dimension sourced from the embedding SSoT (DEFAULT_EMBEDDING_DIM = 1024).
"""
from alembic import op

revision = "0235"
down_revision = "0234"
branch_labels = None
depends_on = None

_HNSW = "ix_semantic_cache_qe_hnsw"


def upgrade() -> None:
    # Drop the HNSW index (bound to the old 1280-dim column), retype the empty
    # column to the canonical 1024-dim, then recreate the index identically.
    op.execute(f"DROP INDEX IF EXISTS {_HNSW}")
    op.execute(
        "ALTER TABLE semantic_cache "
        "ALTER COLUMN query_embedding TYPE vector(1024)"
    )
    op.execute(
        f"CREATE INDEX {_HNSW} ON semantic_cache "
        "USING hnsw (query_embedding vector_cosine_ops) "
        "WITH (m='32', ef_construction='200')"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_HNSW}")
    op.execute(
        "ALTER TABLE semantic_cache "
        "ALTER COLUMN query_embedding TYPE vector(1280)"
    )
    op.execute(
        f"CREATE INDEX {_HNSW} ON semantic_cache "
        "USING hnsw (query_embedding vector_cosine_ops) "
        "WITH (m='32', ef_construction='200')"
    )
