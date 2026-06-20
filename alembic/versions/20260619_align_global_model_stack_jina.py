"""Align GLOBAL system_config model stack to the canonical jina + OpenAI stack.

The chosen stack is already correct at the authoritative resolve layers
(ai_providers = jina+openai only; ai_models = gpt-4.1-nano, gpt-4.1-mini,
jina-embeddings-v3, jina-reranker-v3; every per-bot bot_model_bindings row uses
those four). Only the GLOBAL system_config FALLBACK values had drifted to
out-of-scope models — set out-of-band (no alembic seeds them; grep of
alembic/versions/ = 0 hits), exactly the config drift the project forbids.

Verified 2026-06-19 (live + DB evidence):
  - reranker_model='cohere/rerank-v3.5' under reranker_provider='jina' → the Jina
    rerank API returns HTTP 422 ("does not match expected tags: jina-reranker-*").
    Live A/B: cohere/rerank-v3.5 → 422; jina-reranker-v3 → 200.
  - embedding_model='text-embedding-3-small' (OpenAI, 1536-dim) under
    embedding_provider='jina' is the same provider/model mismatch class.
  - embedding_dimension=1536 contradicts the ACTUAL stored vectors: every row in
    document_chunks.embedding is vector(1024) (1126 rows, vector_dims=1024 — jina
    -embeddings-v3 is 1024-dim). retrieve.py:937 reads embedding_dimension to size
    the query vector, so a global-fallback to 1536 would mismatch the 1024 column.

The live answer path is shielded today because per-bot bindings override the
global value, so this aligns the FALLBACK (and any non-bot / health-check path)
to the same jina stack — removing the landmine without changing the working
per-bot path. The two ``*_alternatives`` lists (zero consumers in src/) are
pruned to jina-only so no out-of-scope model name remains in config.

In-scope OpenAI keys (enrichment_model, multi_query_model, llm_default_model,
contextual_retrieval_model, metadata_extraction_model, deepeval_judge_model =
gpt-4.1-mini) are left untouched — gpt-4.1-mini IS part of the chosen stack.

UPDATE-only (rows already exist); value_type/description preserved. Downgrade
restores the prior (drifted) values verbatim for a faithful, reversible diff.

Revision ID: align_model_stack_jina_20260619
Revises: rls_system_role_grants_20260619
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "align_model_stack_jina_20260619"
down_revision = "rls_system_role_grants_20260619"
branch_labels = None
depends_on = None

# (key, jsonb-literal) — canonical jina stack.
_CANONICAL: tuple[tuple[str, str], ...] = (
    ("reranker_model", '"jina-reranker-v3"'),
    ("embedding_model", '"jina-embeddings-v3"'),
    ("embedding_dimension", "1024"),
    ("reranker_model_alternatives", '["jina-reranker-v3"]'),
    ("embedding_model_alternatives", '["jina-embeddings-v3"]'),
)

# Prior drifted values — restored verbatim on downgrade.
_PRIOR: tuple[tuple[str, str], ...] = (
    ("reranker_model", '"cohere/rerank-v3.5"'),
    ("embedding_model", '"text-embedding-3-small"'),
    ("embedding_dimension", "1536"),
    (
        "reranker_model_alternatives",
        '["cohere/rerank-v3.5", "BAAI/bge-reranker-v2-m3", "viranker"]',
    ),
    (
        "embedding_model_alternatives",
        '["text-embedding-3-small", "BAAI/bge-m3", '
        '"intfloat/multilingual-e5-large-instruct"]',
    ),
)


def _apply(rows: tuple[tuple[str, str], ...]) -> None:
    for key, jval in rows:
        op.execute(
            text(
                "UPDATE system_config SET value = CAST(:v AS jsonb) WHERE key = :k"
            ).bindparams(v=jval, k=key)
        )


def upgrade() -> None:
    _apply(_CANONICAL)


def downgrade() -> None:
    _apply(_PRIOR)
