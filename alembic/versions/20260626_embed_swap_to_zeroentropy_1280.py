"""Swap embedder OpenAI → ZeroEntropy zembed-1 @1280 (OpenAI embed quota burned 429).

Live-verified 2026-06-26: OpenAI ``/v1/embeddings`` returns HTTP 429
``insufficient_quota`` — query embedding hard-fails at the FRONT of the chat
pipeline, so no request reaches the (now Innocom) chat model. The ZeroEntropy
key is alive (rerank uses it) and ``zembed-1`` serves embeddings at
``/v1/models/embed``; its matryoshka dimensions are {2560,1280,640,…} — 1024 is
NOT offered, so the pgvector columns must move 1024 → 1280.

Two parts:
 1. Widen both ``vector(1024)`` columns to ``vector(1280)``. Existing vectors are
    1024-dim (incompatible space AND width) — NULL the chunk vectors (re-ingest
    repopulates) and TRUNCATE the semantic cache (ephemeral). Drop/recreate the
    HNSW indexes (m=32, ef_construction=200, cosine) around the type change.
 2. Point the platform embedder at ZeroEntropy: ``system_config`` provider/model/
    dim, the ``zembed-1`` model row dim, and repoint every embedding binding off
    the (burned) OpenAI ``text-embedding-3-small`` row → ``zembed-1`` @1280.
    Domain-neutral: keyed on purpose='embedding', covers all N bots.

REQUIRES re-ingesting all bots after upgrade (chunk vectors were nulled).
Content-state via tracked migration (sacred-rule 7 — never psql).
"""
from __future__ import annotations

from alembic import op

revision = "embed_swap_ze1280_260626"
down_revision = "chat_swap_innocom_260626"
branch_labels = None
depends_on = None

_ZEMBED_MODEL_ID = "770cc668-e905-47c4-8276-a2382aa40568"   # zembed-1 (ZeroEntropy)
_OPENAI_EMBED_MODEL_ID = "f1f1f1f1-2222-4222-8222-222222222222"  # text-embedding-3-small
_NEW_DIM = 1280
_OLD_DIM = 1024


def _resize(table: str, col: str, index: str, dim: int, *, truncate: bool) -> None:
    op.execute(f"DROP INDEX IF EXISTS {index}")
    if truncate:
        op.execute(f"TRUNCATE {table}")
    else:
        # 1024-dim vectors are incompatible with the new width AND embedding
        # space — clear them so the column can be widened; re-ingest refills.
        op.execute(f"UPDATE {table} SET {col} = NULL WHERE {col} IS NOT NULL")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE vector({dim})")
    op.execute(
        f"CREATE INDEX {index} ON public.{table} "
        f"USING hnsw ({col} vector_cosine_ops) WITH (m='32', ef_construction='200')"
    )


def upgrade() -> None:
    # 1) Widen vector columns 1024 → 1280.
    _resize("document_chunks", "embedding", "ix_chunks_embedding_hnsw",
            _NEW_DIM, truncate=False)
    _resize("semantic_cache", "query_embedding", "ix_semantic_cache_qe_hnsw",
            _NEW_DIM, truncate=True)

    # 2) Point the platform embedder at ZeroEntropy zembed-1 @1280.
    op.execute("UPDATE system_config SET value = '\"zeroentropy\"' WHERE key = 'embedding_provider'")
    op.execute("UPDATE system_config SET value = '\"zembed-1\"' WHERE key = 'embedding_model'")
    op.execute(f"UPDATE system_config SET value = '{_NEW_DIM}' WHERE key = 'embedding_dimension'")
    op.execute(f"UPDATE ai_models SET embedding_dimension = {_NEW_DIM} WHERE id = '{_ZEMBED_MODEL_ID}'")
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = '{_ZEMBED_MODEL_ID}',
            extra_params = '{{"dimension": {_NEW_DIM}}}'::jsonb
        WHERE purpose = 'embedding'
          AND record_model_id = '{_OPENAI_EMBED_MODEL_ID}'
        """,
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = '{_OPENAI_EMBED_MODEL_ID}',
            extra_params = '{{"dimension": {_OLD_DIM}}}'::jsonb
        WHERE purpose = 'embedding'
          AND record_model_id = '{_ZEMBED_MODEL_ID}'
        """,
    )
    op.execute("UPDATE system_config SET value = '\"litellm\"' WHERE key = 'embedding_provider'")
    op.execute("UPDATE system_config SET value = '\"text-embedding-3-small\"' WHERE key = 'embedding_model'")
    op.execute(f"UPDATE system_config SET value = '{_OLD_DIM}' WHERE key = 'embedding_dimension'")
    _resize("document_chunks", "embedding", "ix_chunks_embedding_hnsw",
            _OLD_DIM, truncate=False)
    _resize("semantic_cache", "query_embedding", "ix_semantic_cache_qe_hnsw",
            _OLD_DIM, truncate=True)
