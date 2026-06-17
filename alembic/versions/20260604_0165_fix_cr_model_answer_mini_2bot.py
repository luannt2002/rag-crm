"""Fix Contextual Retrieval model (dead Gemma 502) + answer model nano->mini.

Revision: 0165
Prev:     0164

Root cause (ROOTCAUSE_ALL_FLOWS_20260604.md):
  #1 contextual_retrieval_model = custom_openai/gemma-4-e2b-it → LM Studio
     returns HTTP 502 → enrich fails silently → 0/544 chunks carry context →
     embedding blind → retrieval miss. Switch to working OpenAI gpt-4.1-nano
     (ingest-enrich tier, cheap, runs once per chunk).
  embedding_text_strategy = raw_only → prefix_plus_raw so the embedder sees
     the situated context prefix (Anthropic Contextual Retrieval recipe).
  #2 answer model = gpt-4.1-nano too weak for citation / key-fact extraction
     / LaTeX. Lift llm_primary to gpt-4.1-mini for the two verify bots
     (test-spa-id, thong-tu-09-2020-tt-nhnn) + raise max_tokens to 2048.

Sacred-rule alignment:
  ✅ Pure alembic DML (rule 7 — no psql hot-fix)
  ✅ Reversible — downgrade restores prior values
  ✅ Zero-hardcode — model resolved by name from ai_models, not raw id literal
  ✅ Domain-neutral — bot slugs in alembic DML are exempt (data, not code)

NOTE: re-ingest of the two bots is REQUIRED after upgrade for the CR/embedding
change to take effect on already-stored chunks.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0165"
down_revision: str | None = "0164"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_VERIFY_BOTS = ("test-spa-id", "thong-tu-09-2020-tt-nhnn")


def upgrade() -> None:
    # 1. CR model: dead Gemma → working gpt-4.1-nano
    op.execute(text("""
        UPDATE system_config
        SET value = '"gpt-4.1-nano"', updated_at = NOW()
        WHERE key = 'contextual_retrieval_model'
    """))

    # 2. Embedding sees the context prefix
    op.execute(text("""
        UPDATE system_config
        SET value = '"prefix_plus_raw"', updated_at = NOW()
        WHERE key = 'embedding_text_strategy'
    """))

    # 3. Answer model nano → mini + max_tokens 2048 for the two verify bots
    op.execute(text("""
        UPDATE bot_model_bindings bmb
        SET record_model_id = (
                SELECT id FROM ai_models
                WHERE name = 'gpt-4.1-mini' AND deleted_at IS NULL
                LIMIT 1
            ),
            max_tokens = 2048,
            version = bmb.version + 1,
            updated_at = NOW()
        WHERE bmb.purpose = 'llm_primary'
          AND bmb.active = true
          AND bmb.record_bot_id IN (
              SELECT id FROM bots WHERE bot_id = ANY(:slugs)
          )
    """).bindparams(slugs=list(_VERIFY_BOTS)))


def downgrade() -> None:
    op.execute(text("""
        UPDATE bot_model_bindings bmb
        SET record_model_id = (
                SELECT id FROM ai_models
                WHERE name = 'gpt-4.1-nano' AND deleted_at IS NULL
                LIMIT 1
            ),
            max_tokens = 1024,
            version = bmb.version + 1,
            updated_at = NOW()
        WHERE bmb.purpose = 'llm_primary'
          AND bmb.active = true
          AND bmb.record_bot_id IN (
              SELECT id FROM bots WHERE bot_id = ANY(:slugs)
          )
    """).bindparams(slugs=list(_VERIFY_BOTS)))
    op.execute(text("""
        UPDATE system_config
        SET value = '"raw_only"', updated_at = NOW()
        WHERE key = 'embedding_text_strategy'
    """))
    op.execute(text("""
        UPDATE system_config
        SET value = '"custom_openai/gemma-4-e2b-it"', updated_at = NOW()
        WHERE key = 'contextual_retrieval_model'
    """))
