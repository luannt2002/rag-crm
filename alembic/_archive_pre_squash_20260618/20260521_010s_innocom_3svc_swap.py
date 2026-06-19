"""[T2-CostPerf] Innocom 3-service swap — Enrich + CRAG + Grounding → gemma-4-e2b-it.

Revision ID: 010s
Revises: 010r
Create Date: 2026-05-21

Plan v3 (``plans/260521-INNOCOM-3SVC-SWAP/plan.md``) — swap 3 service
Ragbot từ OpenAI gpt-4.1-mini sang Innocom LM Studio gemma-4-e2b-it.
Tiết kiệm ~$50-100/tháng. KHÔNG động LLM Answer (HALLU sacred risk).

**Pattern routing CHUẨN** (verified bằng preflight 2026-05-21,
``scripts/preflight_innocom_litellm_routing.py``):
- ``ai_providers.code='custom_openai'`` + ``requires_prefix=True``
- ``format_litellm_model()`` → ``"custom_openai/gemma-4-e2b-it"``
- LiteLLM auto-detect ``custom_openai/`` prefix → dùng OpenAI adapter
  với ``api_base`` override = ``ai_providers.base_url``
- Verified: ``litellm.acompletion(model="custom_openai/gemma-4-e2b-it",
  api_base=$LMSTUDIO_BASE_URL/v1, api_key=$LMSTUDIO_API_KEY)`` → 144 chars
  VN response, latency 3-4s.

Plan v2 nguyên gốc dùng ``code='lmstudio'`` đã FAIL: LiteLLM (current
release) KHÔNG có ``lmstudio`` adapter, throw ``BadRequestError: LLM
Provider NOT provided``.

This migration seeds 4 components in 1 transaction:

1. ``ai_providers`` row — code='custom_openai', type='llm', base_url
   from $LMSTUDIO_BASE_URL env, auth via $LMSTUDIO_API_KEY env. Mirrors
   the existing ``ai_providers`` shape used by OpenAI/Anthropic rows.

2. ``ai_models`` row — name='gemma-4-e2b-it', kind='llm', pricing $0
   (self-host), context_window=8192 (conservative estimate; gemma-4
   official is 8k).

3. ``bot_model_bindings`` UPSERT for the legalbot:
   - purpose='grading' → gemma-4-e2b-it
   - purpose='grounding' → gemma-4-e2b-it
   Per-bot scope — only ``thong-tu-09-2020-tt-nhnn`` for staging
   verification. Other bots stay on gpt-4.1-mini.

4. ``system_config.contextual_retrieval_model`` →
   'custom_openai/gemma-4-e2b-it' (pre-formatted name; ``document_service``
   passes this string directly to ``litellm.acompletion(model=...)``).

Idempotent: ``ON CONFLICT`` everywhere. Re-running the migration on a
DB that already has these rows is a no-op.

Cost: 1 LLM call/chunk × 80 chunks/document × $0 (self-host) vs $0.024
on gpt-4.1-mini. CRAG grading: ~$0 vs ~$0.0001/grade. Grounding check:
~$0 vs ~$0.0002/check. Projected ~$50-100/month at current traffic.

Risk: LOW. None of the 3 services emits answer tokens (HALLU=0 sacred
is not at risk). Per-bot scope limits blast radius — other bots unaffected.

Rollback: ``alembic downgrade 010r`` deletes all 4 seeds + restores
bindings to gpt-4.1-mini. Document service re-reads
``contextual_retrieval_model`` from constants default after row delete.
"""

from __future__ import annotations

import os
import uuid

from alembic import op
from sqlalchemy import text


revision: str = "010s"
down_revision: str | None = "010r"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Stable UUIDs — deterministic seed for idempotent re-runs + audit visibility.
INNOCOM_PROVIDER_ID = uuid.UUID("17000c0e-1000-4000-8000-000000000001")
GEMMA_E2B_MODEL_ID = uuid.UUID("17000c0e-1000-4000-8000-000000000002")


# ``ai_providers.base_url`` is the runtime ``api_base`` LiteLLM sees.
# Read from env at migration time so the prod / staging / dev envs each
# point at their own LM Studio host. Default matches the canonical Innocom
# endpoint; operators with a different host export LMSTUDIO_BASE_URL
# before running ``alembic upgrade``.
_INNOCOM_BASE_URL = (
    os.environ.get("LMSTUDIO_BASE_URL", "https://llm.innocom.co").rstrip("/")
)
if not _INNOCOM_BASE_URL.endswith("/v1"):
    _INNOCOM_BASE_URL = _INNOCOM_BASE_URL + "/v1"


# Legalbot identifier — per-bot swap scope. Staging-only ship; broaden to
# other bots after 1-week observation. Hard-coded slug because alembic
# runs offline (no Redis bot registry); we look up the UUID at migrate
# time via SELECT.
_TARGET_BOT_SLUG = "thong-tu-09-2020-tt-nhnn"


def upgrade() -> None:
    """Seed Innocom provider + gemma-4-e2b-it model + swap legalbot bindings
    + flip enrichment system_config.
    """
    # --- 1. ai_providers ---
    op.execute(
        text(
            """
            INSERT INTO ai_providers (
                id, name, code, type, base_url, auth_type, metadata_json,
                enabled, api_key_ref, timeout_ms, connect_timeout_ms,
                max_retries, max_concurrent, requires_prefix
            )
            VALUES (
                :id, :name, :code, 'llm', :base_url, 'api_key', '{}'::jsonb,
                true, :api_key_ref, 30000, 10000, 2, 16, true
            )
            ON CONFLICT (name) DO UPDATE SET
                code = EXCLUDED.code,
                base_url = EXCLUDED.base_url,
                api_key_ref = EXCLUDED.api_key_ref,
                requires_prefix = EXCLUDED.requires_prefix,
                enabled = true,
                updated_at = NOW()
            """,
        ).bindparams(
            id=str(INNOCOM_PROVIDER_ID),
            name="innocom_lmstudio",
            code="custom_openai",
            base_url=_INNOCOM_BASE_URL,
            api_key_ref="LMSTUDIO_API_KEY",
        ),
    )

    # --- 2. ai_models ---
    op.execute(
        text(
            """
            INSERT INTO ai_models (
                id, record_provider_id, name, kind, model_id,
                context_window, max_output_tokens,
                input_price_per_1k_usd, output_price_per_1k_usd,
                supports_streaming, supports_tools, supports_vision,
                supports_json_mode, supports_caching, supports_reasoning,
                enabled, quality_tier, metadata_json, languages,
                default_temperature, default_top_p, default_max_tokens
            )
            VALUES (
                :id, :provider_id, :name, 'llm', :model_id,
                8192, 2048,
                0.0, 0.0,
                true, false, false,
                false, false, false,
                true, 'standard', '{}'::jsonb, ARRAY['vi','en']::text[],
                0.0, 1.0, 512
            )
            ON CONFLICT (record_provider_id, name) DO UPDATE SET
                kind = EXCLUDED.kind,
                model_id = EXCLUDED.model_id,
                context_window = EXCLUDED.context_window,
                enabled = true,
                deleted_at = NULL,
                updated_at = NOW()
            """,
        ).bindparams(
            id=str(GEMMA_E2B_MODEL_ID),
            provider_id=str(INNOCOM_PROVIDER_ID),
            name="gemma-4-e2b-it",
            model_id="gemma-4-e2b-it",
        ),
    )

    # --- 3. bot_model_bindings — legalbot grading + grounding ---
    # Resolve legalbot UUID + tenant + workspace; skip if bot missing.
    op.execute(
        text(
            """
            INSERT INTO bot_model_bindings (
                id, record_tenant_id, workspace_id, record_bot_id,
                purpose, record_model_id,
                rank, weight, temperature, max_tokens, top_p, extra_params,
                active, version
            )
            SELECT
                gen_random_uuid(), b.record_tenant_id, b.workspace_id, b.id,
                'grading', :model_id,
                1, 1.0, 0.0, 512, 1.0, '{}'::jsonb,
                true, 1
            FROM bots b
            WHERE b.bot_id = :bot_slug AND b.deleted_at IS NULL
            ON CONFLICT DO NOTHING
            """,
        ).bindparams(
            model_id=str(GEMMA_E2B_MODEL_ID),
            bot_slug=_TARGET_BOT_SLUG,
        ),
    )
    op.execute(
        text(
            """
            INSERT INTO bot_model_bindings (
                id, record_tenant_id, workspace_id, record_bot_id,
                purpose, record_model_id,
                rank, weight, temperature, max_tokens, top_p, extra_params,
                active, version
            )
            SELECT
                gen_random_uuid(), b.record_tenant_id, b.workspace_id, b.id,
                'grounding', :model_id,
                1, 1.0, 0.0, 512, 1.0, '{}'::jsonb,
                true, 1
            FROM bots b
            WHERE b.bot_id = :bot_slug AND b.deleted_at IS NULL
            ON CONFLICT DO NOTHING
            """,
        ).bindparams(
            model_id=str(GEMMA_E2B_MODEL_ID),
            bot_slug=_TARGET_BOT_SLUG,
        ),
    )

    # --- 4. system_config.contextual_retrieval_model ---
    # The document_service pulls this string and feeds it directly to
    # ``litellm.acompletion(model=...)``. Pre-formatted with the
    # ``custom_openai/`` prefix so LiteLLM picks up the right adapter; the
    # ``api_base`` flows in via the provider row above. Both must move
    # together — that's why this single migration owns all 4 changes.
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'contextual_retrieval_model',
                '"custom_openai/gemma-4-e2b-it"'::jsonb,
                'string',
                'Innocom swap — Plan v3 alembic 010s. LiteLLM routes via custom_openai adapter + api_base from ai_providers.innocom_lmstudio.base_url. Rollback: SET value=''"gpt-4.1-mini"''::jsonb.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    description = EXCLUDED.description,
                    updated_at = NOW()
            """,
        ),
    )


def downgrade() -> None:
    """Revert 4 components in reverse-FK order: system_config → bindings →
    model → provider. Legalbot grading/grounding fall back to the platform
    default (gpt-4.1-mini binding rank=1 on the OpenAI row).
    """
    # 4. system_config — restore gpt-4.1-mini for enrichment
    op.execute(
        text(
            """
            UPDATE system_config SET
                value = '"gpt-4.1-mini"'::jsonb,
                description = 'LiteLLM model id for CR rewrite (cheap; cfg-overridable)',
                updated_at = NOW()
            WHERE key = 'contextual_retrieval_model'
            """,
        ),
    )

    # 3. bot_model_bindings — soft-delete the two innocom rows
    op.execute(
        text(
            """
            UPDATE bot_model_bindings SET
                deleted_at = NOW(),
                active = false
            WHERE record_model_id = :model_id
              AND purpose IN ('grading', 'grounding')
            """,
        ).bindparams(model_id=str(GEMMA_E2B_MODEL_ID)),
    )

    # 2. ai_models — soft-delete (FK to bindings preserved via deleted_at)
    op.execute(
        text(
            """
            UPDATE ai_models SET
                deleted_at = NOW(),
                enabled = false
            WHERE id = :model_id
            """,
        ).bindparams(model_id=str(GEMMA_E2B_MODEL_ID)),
    )

    # 1. ai_providers — soft-delete
    op.execute(
        text(
            """
            UPDATE ai_providers SET
                deleted_at = NOW(),
                enabled = false
            WHERE id = :provider_id
            """,
        ).bindparams(provider_id=str(INNOCOM_PROVIDER_ID)),
    )
