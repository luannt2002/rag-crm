"""Swap platform embedder jina → OpenAI text-embedding-3-small @1024 (Jina keys burned).

Live-verified 2026-06-26: the Jina embedding key returns HTTP 403 / AUTHZ_INSUFFICIENT_BALANCE
(same burned account as the reranker). The OpenAI key in env tests 200 OK on
text-embedding-3-small, and litellm can request ``dimensions=1024`` (matryoshka) so the
produced vector matches the existing 1024-dim pgvector column — NO schema change.

Provider ``litellm`` routes ``text-embedding-3-small`` to OpenAI via the standard
litellm convention; ``litellm_embedder`` now passes ``dimensions`` for text-embedding-3-*.

REQUIRES re-embedding the corpus: existing vectors are Jina-1024 (different space), so all
3 bots must be re-ingested after this migration. Content-state via tracked migration
(sacred-rule 7 — never psql).
"""
from __future__ import annotations

from alembic import op

revision = "embed_swap_openai_260626"
down_revision = "rerank_swap_zeroentropy_260626"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE system_config SET value = '\"litellm\"' WHERE key = 'embedding_provider'",
    )
    op.execute(
        "UPDATE system_config SET value = '\"text-embedding-3-small\"' "
        "WHERE key = 'embedding_model'",
    )
    op.execute(
        "UPDATE system_config SET value = '1024' WHERE key = 'embedding_dimension'",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE system_config SET value = '\"jina\"' WHERE key = 'embedding_provider'",
    )
    op.execute(
        "UPDATE system_config SET value = '\"jina-embeddings-v3\"' "
        "WHERE key = 'embedding_model'",
    )
    op.execute(
        "UPDATE system_config SET value = '1024' WHERE key = 'embedding_dimension'",
    )
