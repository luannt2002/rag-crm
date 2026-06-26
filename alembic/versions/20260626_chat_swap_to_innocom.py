"""Swap chat/LLM provider OpenAI → Innocom gateway (OpenAI quota burned 429).

Live-verified 2026-06-26: BOTH OpenAI chat (gpt-4.1-mini) and embed return HTTP
429 ``insufficient_quota`` — the LLM answer path is dead. The Innocom gateway at
``https://ai.innocom.co/v1/chat/completions`` is OpenAI-compatible and returns
200 from this server's egress IP (the older ``llm.innocom.co`` host was blocked
by Cloudflare 1010 / now 502 — different hostname, different WAF policy).

Mechanism (no Python edit, per ``format_litellm_model`` policy):
- New ``innocom`` provider row: base_url ``https://ai.innocom.co/v1``, key via
  ``INNOCOM_API_KEY`` env (``api_key_ref`` → ``env:INNOCOM_API_KEY``).
- New chat model row ``openai/claude``: the explicit ``openai/`` prefix is
  passthrough-authoritative in ``format_litellm_model`` (the "/" branch), so
  LiteLLM drives its OpenAI client against ``api_base`` = Innocom. The gateway
  accepts ``claude`` as an alias and routes to its backing model.
- Repoint EVERY binding currently on any ``gpt-4.1-mini`` row → the Innocom
  model. Domain-neutral: keyed on the model row, not on any bot_id, so it covers
  all N bots uniformly. Embedding (text-embedding-3-small) and ingest enrichment
  (gpt-4.1-nano) rows are untouched — embed has no Innocom equivalent (gateway
  ``/v1/embeddings`` → 404) and stays on OpenAI until quota is restored.

Content-state via tracked migration (sacred-rule 7 — never psql).
"""
from __future__ import annotations

from alembic import op

revision = "chat_swap_innocom_260626"
down_revision = "rebind_embedding_openai_260626"
branch_labels = None
depends_on = None

_INNOCOM_PROVIDER_ID = "a1b2c3d4-0000-4000-8000-000000000c01"
_INNOCOM_MODEL_ID = "a1b2c3d4-0000-4000-8000-000000000c02"
_OPENAI_CHAT_MODEL_ID = "aa25f11d-dbf9-4d23-9c5c-29caf329d498"  # gpt-4.1-mini
_OPENAI_PROVIDER_ID = "2b771241-a6f6-4e85-bd23-0410abb9cf3d"


def upgrade() -> None:
    # 1) Innocom provider (OpenAI-compatible gateway). Mirror every NOT NULL
    #    column (timeout_ms / connect_timeout_ms / max_retries / max_concurrent)
    #    from the existing OpenAI provider row; override identity + endpoint only.
    op.execute(
        f"""
        INSERT INTO ai_providers
          (id, name, code, type, base_url, auth_type, api_key_ref, api_key_encrypted,
           requires_prefix, enabled, metadata_json, timeout_ms, connect_timeout_ms,
           max_retries, max_concurrent, created_at, updated_at)
        SELECT '{_INNOCOM_PROVIDER_ID}', 'innocom', 'innocom', type,
               'https://ai.innocom.co/v1', auth_type, 'INNOCOM_API_KEY', NULL,
               false, true, '{{}}'::jsonb, timeout_ms, connect_timeout_ms,
               max_retries, max_concurrent, now(), now()
        FROM ai_providers WHERE id = '{_OPENAI_PROVIDER_ID}'
        ON CONFLICT (id) DO UPDATE
          SET base_url = EXCLUDED.base_url,
              api_key_ref = EXCLUDED.api_key_ref,
              enabled = true
        """,
    )
    # 2) Innocom chat model row — mirror every NOT NULL column from the existing
    #    gpt-4.1-mini row so the schema contract is satisfied without enumerating
    #    each column by hand. Override id / provider / wire-name only.
    op.execute(
        f"""
        INSERT INTO ai_models
          (id, record_provider_id, name, model_id, kind, context_window,
           max_output_tokens, input_price_per_1k_usd, output_price_per_1k_usd,
           supports_streaming, supports_tools, supports_vision, supports_json_mode,
           languages, metadata_json, enabled, quality_tier, supports_caching,
           supports_reasoning, created_at, updated_at)
        SELECT '{_INNOCOM_MODEL_ID}', '{_INNOCOM_PROVIDER_ID}',
               'openai/claude', 'openai/claude', kind, context_window,
               max_output_tokens, input_price_per_1k_usd, output_price_per_1k_usd,
               supports_streaming, supports_tools, supports_vision, supports_json_mode,
               languages, metadata_json, true, quality_tier, supports_caching,
               supports_reasoning, now(), now()
        FROM ai_models WHERE id = '{_OPENAI_CHAT_MODEL_ID}'
        ON CONFLICT (id) DO NOTHING
        """,
    )
    # 3) Repoint all chat/LLM bindings off the burned OpenAI model → Innocom.
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = '{_INNOCOM_MODEL_ID}'
        WHERE record_model_id = '{_OPENAI_CHAT_MODEL_ID}'
        """,
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = '{_OPENAI_CHAT_MODEL_ID}'
        WHERE record_model_id = '{_INNOCOM_MODEL_ID}'
        """,
    )
    op.execute(f"DELETE FROM ai_models WHERE id = '{_INNOCOM_MODEL_ID}'")
    op.execute(f"DELETE FROM ai_providers WHERE id = '{_INNOCOM_PROVIDER_ID}'")
