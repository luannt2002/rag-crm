"""Switch embedding + rerank provider ZeroEntropy -> Jina.

embedding: jina-embeddings-v3 (1024-dim, multilingual, Vietnamese in top-30) with
late_chunking (cross-chunk context in the embedding pass, ZERO generative-LLM
calls) — replaces the per-chunk nano Contextual-Retrieval enrichment that was the
O(n^2) ingest bottleneck. rerank: jina-reranker-v3 (listwise multilingual).

Scope:
* Seed ``ai_providers('jina_ai')`` + ``ai_models`` jina-embeddings-v3 / jina-reranker-v3.
* Repoint ``bot_model_bindings`` (purpose embedding/rerank) at the Jina models.
* Flip ``system_config``: embedding_provider/model/dimension, reranker_provider,
  and contextual_retrieval_enabled=false (late_chunking supersedes nano CR).
* Migrate ``document_chunks.embedding`` vector(1280) -> vector(1024) + rebuild HNSW.
  The table is empty at migration time (corpus re-ingested fresh on Jina); an
  embedding-dimension change is inherently a re-embed, not an in-place cast.

Reversible: downgrade restores ZeroEntropy config + 1280-dim column. The seeded
Jina provider/model rows are left in place on downgrade (harmless, unreferenced).
"""
from alembic import op

revision = "0228"
down_revision = "0227"
branch_labels = None
depends_on = None

_PROVIDER_ID = "f1f1f1f1-1111-4111-8111-111111111111"
_EMBED_MODEL_ID = "f1f1f1f1-2222-4222-8222-222222222222"
_RERANK_MODEL_ID = "f1f1f1f1-3333-4333-8333-333333333333"


def upgrade() -> None:
    # 1. Jina provider (idempotent via WHERE NOT EXISTS — no unique on code assumed).
    op.execute(
        f"""
        INSERT INTO ai_providers
          (id, name, type, base_url, auth_type, metadata_json, enabled, code,
           api_key_ref, timeout_ms, connect_timeout_ms, max_retries, max_concurrent,
           requires_prefix, created_at, updated_at)
        SELECT '{_PROVIDER_ID}', 'Jina AI', 'external_api', 'https://api.jina.ai',
               'bearer', '{{}}'::jsonb, true, 'jina_ai',
               'JINA_API_KEY', 60000, 5000, 3, 10, true, now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM ai_providers WHERE code = 'jina_ai')
        """
    )

    # 2. Jina models (embedding + reranker). All NOT-NULL columns set.
    op.execute(
        f"""
        INSERT INTO ai_models
          (id, record_provider_id, name, kind, model_id, embedding_dimension,
           context_window, max_output_tokens, input_price_per_1k_usd,
           output_price_per_1k_usd, supports_streaming, supports_tools,
           supports_vision, supports_json_mode, languages, metadata_json,
           enabled, quality_tier, supports_caching, supports_reasoning,
           created_at, updated_at)
        SELECT '{_EMBED_MODEL_ID}',
               (SELECT id FROM ai_providers WHERE code = 'jina_ai'),
               'jina-embeddings-v3', 'embedding', 'jina-embeddings-v3', 1024,
               8192, 0, 0.000020, 0.000000,
               false, false, false, false, '{{vi,en}}'::varchar[], '{{}}'::jsonb,
               true, 'standard', false, false, now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM ai_models WHERE id = '{_EMBED_MODEL_ID}')
        """
    )
    op.execute(
        f"""
        INSERT INTO ai_models
          (id, record_provider_id, name, kind, model_id, embedding_dimension,
           context_window, max_output_tokens, input_price_per_1k_usd,
           output_price_per_1k_usd, supports_streaming, supports_tools,
           supports_vision, supports_json_mode, languages, metadata_json,
           enabled, quality_tier, supports_caching, supports_reasoning,
           created_at, updated_at)
        SELECT '{_RERANK_MODEL_ID}',
               (SELECT id FROM ai_providers WHERE code = 'jina_ai'),
               'jina-reranker-v3', 'reranker', 'jina-reranker-v3', NULL,
               131072, 0, 0.000020, 0.000000,
               false, false, false, false, '{{vi,en}}'::varchar[], '{{}}'::jsonb,
               true, 'standard', false, false, now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM ai_models WHERE id = '{_RERANK_MODEL_ID}')
        """
    )

    # 3. Repoint per-bot bindings at the Jina models.
    op.execute(
        f"UPDATE bot_model_bindings SET record_model_id = '{_EMBED_MODEL_ID}', "
        f"updated_at = now() WHERE purpose = 'embedding'"
    )
    op.execute(
        f"UPDATE bot_model_bindings SET record_model_id = '{_RERANK_MODEL_ID}', "
        f"updated_at = now() WHERE purpose = 'rerank'"
    )

    # 4. Flip platform defaults (system_config is jsonb-valued).
    op.execute("UPDATE system_config SET value = '\"jina\"'::jsonb WHERE key = 'embedding_provider'")
    op.execute("UPDATE system_config SET value = '\"jina-embeddings-v3\"'::jsonb WHERE key = 'embedding_model'")
    op.execute("UPDATE system_config SET value = '1024'::jsonb WHERE key = 'embedding_dimension'")
    op.execute("UPDATE system_config SET value = '\"jina\"'::jsonb WHERE key = 'reranker_provider'")
    op.execute("UPDATE system_config SET value = 'false'::jsonb WHERE key = 'contextual_retrieval_enabled'")

    # 5. Vector column 1280 -> 1024 + rebuild HNSW (table empty at migration time).
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1024)")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m='32', ef_construction='200')"
    )


def downgrade() -> None:
    # Restore ZeroEntropy config + 1280-dim column. Seeded Jina rows stay
    # (unreferenced once bindings flip back) — re-running upgrade is idempotent.
    op.execute(
        "UPDATE bot_model_bindings SET record_model_id = "
        "(SELECT id FROM ai_models WHERE name = 'zembed-1'), updated_at = now() "
        "WHERE purpose = 'embedding'"
    )
    op.execute(
        "UPDATE bot_model_bindings SET record_model_id = "
        "(SELECT id FROM ai_models WHERE name = 'zerank-2'), updated_at = now() "
        "WHERE purpose = 'rerank'"
    )
    op.execute("UPDATE system_config SET value = '\"zeroentropy\"'::jsonb WHERE key = 'embedding_provider'")
    op.execute("UPDATE system_config SET value = '\"zembed-1\"'::jsonb WHERE key = 'embedding_model'")
    op.execute("UPDATE system_config SET value = '1280'::jsonb WHERE key = 'embedding_dimension'")
    op.execute("UPDATE system_config SET value = '\"zeroentropy\"'::jsonb WHERE key = 'reranker_provider'")
    op.execute("UPDATE system_config SET value = 'true'::jsonb WHERE key = 'contextual_retrieval_enabled'")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1280)")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m='32', ef_construction='200')"
    )
