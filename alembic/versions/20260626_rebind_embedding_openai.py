"""Re-create OpenAI embedding bindings (resolve_embedding REQUIRES a binding).

``ModelResolver.resolve_embedding`` raises InvariantViolation when a bot has no
embedding binding, and ``resolve_runtime`` falls back to the LLM PRIMARY model
(gpt-4.1-mini) — so after the burned-Jina bindings were dropped, query-time
embedding resolved to a chat model → litellm embed 503. Embedding has no
system_config fallback (unlike rerank), so each bot needs an explicit binding.

Fix: repoint the embedding ai_models row (was jina-embeddings-v3) to OpenAI
text-embedding-3-small @1024 and re-create the embedding bindings for the live
bots → that model. Rerank stays on the system_config fallback (ZeroEntropy).

Content-state via tracked migration (sacred-rule 7).
"""
from __future__ import annotations

from alembic import op

revision = "rebind_embedding_openai_260626"
down_revision = "drop_jina_bindings_260626"
branch_labels = None
depends_on = None

_OPENAI_PROVIDER_ID = "2b771241-a6f6-4e85-bd23-0410abb9cf3d"
_EMBED_MODEL_ID = "f1f1f1f1-2222-4222-8222-222222222222"


def upgrade() -> None:
    # Repurpose the embedding ai_models row jina → OpenAI text-embedding-3-small.
    op.execute(
        f"""
        UPDATE ai_models
        SET name = 'text-embedding-3-small',
            model_id = 'text-embedding-3-small',
            record_provider_id = '{_OPENAI_PROVIDER_ID}',
            embedding_dimension = 1024
        WHERE id = '{_EMBED_MODEL_ID}'
        """,
    )
    # Re-create per-bot embedding bindings → the (now OpenAI) model row.
    op.execute(
        f"""
        INSERT INTO bot_model_bindings
          (id, record_tenant_id, workspace_id, record_bot_id, purpose,
           record_model_id, rank, weight, temperature, max_tokens, top_p,
           extra_params, active, version)
        SELECT gen_random_uuid(), b.record_tenant_id, b.workspace_id, b.id,
               'embedding', '{_EMBED_MODEL_ID}', 0, 100, 0, 0, 1,
               '{{"dimension": 1024}}'::jsonb, true, 1
        FROM bots b
        WHERE b.bot_id IN
              ('chinh-sach-xe', 'test-spa-id', 'thong-tu-09-2020-tt-nhnn')
          AND NOT EXISTS (
              SELECT 1 FROM bot_model_bindings x
              WHERE x.record_bot_id = b.id AND x.purpose = 'embedding'
          )
        """,
    )


def downgrade() -> None:
    pass  # see rebind rationale; reversing re-breaks retrieval
