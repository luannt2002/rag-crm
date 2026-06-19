"""ZeroEntropy zembed-1 swap: vector(1024) → vector(2560) + seed system_config.

Migration 0085. Companion to the ZeroEntropy embedder Strategy adapter
(``src/ragbot/infrastructure/embedding/zeroentropy_embedder.py``).

This migration:
1. Drops the HNSW index on ``document_chunks.embedding`` (vector index is
   typmod-coupled and rebuilds in seconds on an empty/null column).
2. Re-shapes the column from ``vector(1024)`` to ``vector(2560)`` and
   NULLs every embedding — Jina v3 vectors are not comparable with
   zembed-1 vectors; mixing them at retrieve time gives garbage scores.
   Re-ingest is mandatory; ops should DELETE the affected bot's chunks
   and re-run document upload before serving traffic.
3. Re-creates the HNSW index with the same cosine_ops + m=32 ef=200
   tuning the system previously used.
4. Seeds ``system_config`` rows so ops can flip provider via a single
   ``UPDATE`` without redeploy:
   * ``embedding_provider`` = "zeroentropy" (default, swap from "litellm")
   * ``embedding_dimension`` = 2560
   * ``enrichment_model`` = "claude-haiku-4-5" (U5 ingest enrichment)

Rollback: re-shapes column back to vector(1024), restores default
``embedding_provider="litellm"`` and ``embedding_dimension=1024``.
ALL embeddings are wiped on rollback too — re-ingest required either way.

CLAUDE.md compliance:
* Zero-hardcode — dim constants live in shared/constants.py
  (DEFAULT_ZEROENTROPY_EMBEDDING_DIM = 2560).
* Strategy + DI — adapter is a one-file drop-in; this migration only
  flips the system_config row that the registry reads.
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


_HNSW_INDEX_NAME = "ix_chunks_embedding_hnsw"
_NEW_DIM = 1280  # zembed-1 matryoshka — fits pgvector HNSW 2000-dim limit
_OLD_DIM = 1024

_SEED_UPGRADE: tuple[tuple[str, object, str, str], ...] = (
    (
        "embedding_provider",
        "zeroentropy",
        "string",
        "Embedder strategy key. Values: 'litellm' (OpenAI/Jina via LiteLLM), "
        "'zeroentropy' (direct HTTP, 2560-dim zembed-1), 'jina' (alias of "
        "litellm). Flip + restart api to swap.",
    ),
    (
        "embedding_dimension",
        _NEW_DIM,
        "int",
        "Embedding vector dimension. MUST match the column type on "
        "document_chunks.embedding (managed by alembic).",
    ),
    (
        "embedding_text_strategy",
        "raw_only",
        "string",
        "Embedding text strategy. 'raw_only' bypasses the enriched-prefix "
        "concat (default for ZeroEntropy which already handles long "
        "context natively). 'prefix_plus_raw' restores the Jina-era pattern.",
    ),
    (
        "enrichment_model",
        "claude-haiku-4-5",
        "string",
        "Per-chunk contextual-enrichment LLM (Anthropic CR pattern). "
        "Haiku 4.5 is the cost-efficient choice for ingest-only enrichment.",
    ),
)

_SEED_DOWNGRADE: tuple[tuple[str, object, str, str], ...] = (
    (
        "embedding_provider",
        "litellm",
        "string",
        "Rollback to LiteLLM embedder (legacy default).",
    ),
    (
        "embedding_dimension",
        _OLD_DIM,
        "int",
        "Rollback embedding dim to Jina v3 (1024).",
    ),
    (
        "embedding_text_strategy",
        "prefix_plus_raw",
        "string",
        "Rollback to prefix_plus_raw embedding text strategy.",
    ),
    (
        "enrichment_model",
        "gpt-4.1-mini",
        "string",
        "Rollback enrichment LLM to gpt-4.1-mini.",
    ),
)


def _seed_rows(rows: tuple[tuple[str, object, str, str], ...]) -> None:
    for key, value, value_type, description in rows:
        json_value = json.dumps(value)
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, (:value)::jsonb, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=key,
                value=json_value,
                value_type=value_type,
                description=description,
            )
        )


def upgrade() -> None:
    # 1. Drop HNSW (vector index is typmod-coupled).
    op.execute(text(f"DROP INDEX IF EXISTS {_HNSW_INDEX_NAME}"))

    # 2. Wipe embeddings — Jina v3 and zembed-1 vectors are not comparable.
    #    Re-ingest is required; this prevents serving stale 1024-dim vectors
    #    via the new index (which would be a dim mismatch error anyway).
    op.execute(text("UPDATE document_chunks SET embedding = NULL"))

    # 3. Re-shape column to the new dimension. ``USING NULL`` is safe here
    #    because step 2 wiped every value first.
    op.execute(
        text(
            f"ALTER TABLE document_chunks "
            f"ALTER COLUMN embedding TYPE vector({_NEW_DIM}) USING NULL"
        )
    )

    # 4. Recreate HNSW with cosine_ops + the same tuning as before.
    op.execute(
        text(
            f"CREATE INDEX {_HNSW_INDEX_NAME} ON document_chunks "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m=32, ef_construction=200)"
        )
    )

    # 5. Seed system_config rows so ops can audit + flip provider.
    _seed_rows(_SEED_UPGRADE)


def downgrade() -> None:
    op.execute(text(f"DROP INDEX IF EXISTS {_HNSW_INDEX_NAME}"))
    op.execute(text("UPDATE document_chunks SET embedding = NULL"))
    op.execute(
        text(
            f"ALTER TABLE document_chunks "
            f"ALTER COLUMN embedding TYPE vector({_OLD_DIM}) USING NULL"
        )
    )
    op.execute(
        text(
            f"CREATE INDEX {_HNSW_INDEX_NAME} ON document_chunks "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m=32, ef_construction=200)"
        )
    )
    _seed_rows(_SEED_DOWNGRADE)
