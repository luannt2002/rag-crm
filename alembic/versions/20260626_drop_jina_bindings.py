"""Remove all per-bot Jina embedding/rerank bindings (keys burned 403/1010).

The per-bot ``bot_model_bindings`` (embedding → jina-embeddings-v3, rerank →
jina-reranker-v3) OVERRIDE ``system_config`` — so even after the platform default
was swapped to OpenAI embed + ZeroEntropy rerank, every bot still resolved to the
now-burned Jina account at query AND ingest (AUTHZ_INSUFFICIENT_BALANCE).

Drop the Jina embedding/rerank bindings so the resolver falls back to the platform
default (system_config + ``_lookup_platform_default``): OpenAI text-embedding-3-small
@1024 for embed, zerank-2 for rerank. Non-Jina bindings (e.g. LLM) are untouched.

Content-state via tracked migration (sacred-rule 7). Irreversible cleanup — the
dropped bindings pointed at burned keys; reversing would re-break retrieval, so
downgrade is a documented no-op.
"""
from __future__ import annotations

from alembic import op

revision = "drop_jina_bindings_260626"
down_revision = "embed_swap_openai_260626"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM bot_model_bindings bmb
        USING ai_models m
        JOIN ai_providers p ON m.record_provider_id = p.id
        WHERE bmb.record_model_id = m.id
          AND p.code = 'jina_ai'
          AND bmb.purpose IN ('embedding', 'rerank')
        """,
    )


def downgrade() -> None:
    # No-op: the removed bindings referenced burned Jina keys; re-inserting them
    # would re-break retrieval. Re-bind via the admin UI if Jina is topped up.
    pass
