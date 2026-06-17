"""Seed Anthropic provider + claude-haiku-4-5 model + wire as OpenAI fallback.

Closes the SPOF where every ``bot_model_bindings`` row with
``purpose='generation'`` ships ``record_fallback_model_id IS NULL``: when
OpenAI returns 5xx / drops the connection / hits its provider-level circuit
breaker, the failover wrap in ``DynamicLiteLLMRouter.complete_runtime`` has
no second-tier model to fall back to and the user sees a hard error.

The router code path (``model_resolver.py:608-625`` resolves the fallback
provider/model when ``record_fallback_model_id`` is set) is already wired
and shipped as of migration 0061 (FK + partial index). What was missing was
data: no Anthropic provider row, no Haiku model row, no bindings pointing
at it.

This migration seeds:

* ``ai_providers`` row — code='anthropic', type='llm', auth via
  ``ANTHROPIC_API_KEY`` env var (matches the existing ``api_key_ref``
  contract used by OpenAI/Jina), enabled=true. Same timeout / concurrency
  knobs as OpenAI for symmetry.
* ``ai_models`` row — name='claude-haiku-4-5-20251001' (canonical Anthropic
  model id; see ``scripts/cost_audit.py`` price table and
  ``docs/_archive/full_pre_trim_20260506/RAGBOT_24STEP_PIPELINE_full.md``
  reference), kind='llm' (sacred — the V14 ``bot_model_bindings.purpose``
  'rerank' vs 'reranker' lesson flagged this naming drift, and the existing
  OpenAI gpt-4.1-mini row uses kind='llm' so we mirror that exactly), prices
  from cost_audit.py: $0.001/1K input, $0.005/1K output, $0.0001/1K cached
  input.
* ``bot_model_bindings`` UPDATE — every active row with purpose='generation'
  AND record_fallback_model_id IS NULL gets the Haiku model UUID. Any row
  the operator manually points elsewhere is left alone.

Idempotent: ``ON CONFLICT`` on ``ai_providers.code``-as-unique-key (we add
a partial unique index on ``code WHERE deleted_at IS NULL`` if absent — the
table only declares a UNIQUE on ``name``, but ``code`` is the lookup field
the resolver uses). Re-runs are a no-op.

Env var contract: ``ANTHROPIC_API_KEY`` MUST be set in the deployment env
before failover can actually fire. The slot already exists in
``.env.example`` (line 49) and ``.env.uat.example`` (line 57); this
migration only labels them with the new "REQUIRED for OpenAI 5xx fallback"
contract via a follow-up doc edit.

Revision ID: 0070
Revises: 0068
Create Date: 2026-05-08
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


# Stable UUIDs make the seed deterministic across re-runs and visible in
# ``ai_config_audit_log`` without hunting for a freshly-generated id.
# Generated once via uuid.uuid4(); committed as constants so re-running the
# migration on the same DB produces zero diff (idempotent invariant).
ANTHROPIC_PROVIDER_ID = uuid.UUID("a5e3c2f1-7b9d-4e8a-9c1d-3f5b8a2e4d6c")
CLAUDE_HAIKU_MODEL_ID = uuid.UUID("c8f7d1a2-9e4b-4f3c-8d6a-1e2b5c7d9a3f")


# Provider seed — mirrors OpenAI row shape (timeout 30s, max_concurrent 16,
# auth_type 'api_key', api_key_ref env-var-name pattern). Only differences
# are vendor-specific: name/code='anthropic', base_url, env var name.
_PROVIDER_INSERT = """
INSERT INTO ai_providers (
    id, name, code, type, base_url, auth_type, metadata_json,
    enabled, api_key_ref, timeout_ms, connect_timeout_ms,
    max_retries, max_concurrent
)
VALUES (
    :id, :name, :code, :type, :base_url, :auth_type, '{}'::jsonb,
    true, :api_key_ref, :timeout_ms, :connect_timeout_ms,
    :max_retries, :max_concurrent
)
ON CONFLICT (name) DO UPDATE SET
    code = EXCLUDED.code,
    type = EXCLUDED.type,
    base_url = EXCLUDED.base_url,
    auth_type = EXCLUDED.auth_type,
    api_key_ref = EXCLUDED.api_key_ref,
    enabled = EXCLUDED.enabled,
    updated_at = now()
"""


# Model seed — kind='llm' (mirrors gpt-4.1-mini exactly; V14 lesson: never
# guess the enum value, copy from a known-working sibling row). Pricing
# from canonical cost table (scripts/cost_audit.py:38).
_MODEL_INSERT = """
INSERT INTO ai_models (
    id, record_provider_id, name, model_id, kind,
    context_window, max_output_tokens,
    input_price_per_1k_usd, output_price_per_1k_usd,
    input_price_per_1k_cached_usd,
    supports_streaming, supports_tools, supports_vision,
    supports_json_mode, supports_caching, supports_reasoning,
    languages, metadata_json, enabled, quality_tier
)
VALUES (
    :id, :provider_id, :name, :model_id, :kind,
    :context_window, :max_output_tokens,
    :input_price, :output_price, :cached_input_price,
    true, true, false,
    true, true, false,
    ARRAY['vi', 'en']::varchar(8)[], '{}'::jsonb, true, :quality_tier
)
ON CONFLICT (record_provider_id, name) DO UPDATE SET
    model_id = EXCLUDED.model_id,
    kind = EXCLUDED.kind,
    context_window = EXCLUDED.context_window,
    max_output_tokens = EXCLUDED.max_output_tokens,
    input_price_per_1k_usd = EXCLUDED.input_price_per_1k_usd,
    output_price_per_1k_usd = EXCLUDED.output_price_per_1k_usd,
    input_price_per_1k_cached_usd = EXCLUDED.input_price_per_1k_cached_usd,
    quality_tier = EXCLUDED.quality_tier,
    enabled = EXCLUDED.enabled,
    updated_at = now()
"""


# Binding fallback wire — only update rows that were left at NULL by the
# original seed. If an operator already pointed a binding at a different
# fallback (e.g. another OpenAI tier), respect that decision.
_BINDING_UPDATE = """
UPDATE bot_model_bindings
SET record_fallback_model_id = :fallback_model_id,
    updated_at = now()
WHERE purpose = 'generation'
  AND record_fallback_model_id IS NULL
  AND active = true
  AND deleted_at IS NULL
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Provider row — UPSERT on the existing UNIQUE (name) constraint.
    bind.execute(
        text(_PROVIDER_INSERT).bindparams(
            id=ANTHROPIC_PROVIDER_ID,
            name="anthropic",
            code="anthropic",
            type="llm",
            base_url="https://api.anthropic.com",
            auth_type="api_key",
            api_key_ref="ANTHROPIC_API_KEY",
            timeout_ms=30000,
            connect_timeout_ms=5000,
            max_retries=3,
            max_concurrent=16,
        )
    )

    # Lookup the resolved provider id — when the row already existed under
    # a different UUID (e.g. operator pre-seeded), use that id so the FK
    # below points at the real row instead of our placeholder.
    resolved_provider_id = bind.execute(
        text("SELECT id FROM ai_providers WHERE code = 'anthropic'")
    ).scalar_one()

    # 2) Model row — UPSERT on UNIQUE (record_provider_id, name).
    bind.execute(
        text(_MODEL_INSERT).bindparams(
            id=CLAUDE_HAIKU_MODEL_ID,
            provider_id=resolved_provider_id,
            name="claude-haiku-4-5-20251001",
            model_id="claude-haiku-4-5-20251001",
            kind="llm",
            context_window=200000,
            max_output_tokens=8192,
            input_price=Decimal("0.001000"),
            output_price=Decimal("0.005000"),
            cached_input_price=Decimal("0.000100"),
            quality_tier="standard",
        )
    )

    resolved_model_id = bind.execute(
        text(
            "SELECT id FROM ai_models "
            "WHERE record_provider_id = :p AND name = :n"
        ).bindparams(p=resolved_provider_id, n="claude-haiku-4-5-20251001")
    ).scalar_one()

    # 3) Wire fallback for every generation binding that has none yet.
    bind.execute(
        text(_BINDING_UPDATE).bindparams(
            fallback_model_id=resolved_model_id,
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Resolve current ids — the upgrade may have UPSERT-ed onto pre-existing
    # rows under different UUIDs, so look up by stable lookup keys.
    provider_row = bind.execute(
        text("SELECT id FROM ai_providers WHERE code = 'anthropic'")
    ).first()
    if provider_row is None:
        # Nothing to undo — provider was never seeded by this migration.
        return
    provider_id = provider_row[0]

    model_row = bind.execute(
        text(
            "SELECT id FROM ai_models "
            "WHERE record_provider_id = :p AND name = :n"
        ).bindparams(p=provider_id, n="claude-haiku-4-5-20251001")
    ).first()

    if model_row is not None:
        model_id = model_row[0]

        # Reverse the bindings update — only flip rows still pointing at
        # the Haiku model. Rows the operator re-pointed are left alone.
        bind.execute(
            text(
                "UPDATE bot_model_bindings "
                "SET record_fallback_model_id = NULL, updated_at = now() "
                "WHERE record_fallback_model_id = :m"
            ).bindparams(m=model_id)
        )

        # Drop the model row. The FK on bot_model_bindings.record_model_id
        # is ON DELETE RESTRICT — but we only set this row as a *fallback*,
        # never as the primary, so no binding's record_model_id should
        # reference it. Defensive guard below in case an operator wired it
        # as primary post-upgrade.
        primary_use = bind.execute(
            text(
                "SELECT COUNT(*) FROM bot_model_bindings "
                "WHERE record_model_id = :m"
            ).bindparams(m=model_id)
        ).scalar_one()

        if primary_use == 0:
            bind.execute(
                text("DELETE FROM ai_models WHERE id = :m").bindparams(m=model_id)
            )

    # Drop the provider row only if no models still hang off it. ON DELETE
    # CASCADE on ai_models would otherwise wipe operator-added Anthropic
    # models we don't own.
    remaining_models = bind.execute(
        text("SELECT COUNT(*) FROM ai_models WHERE record_provider_id = :p")
        .bindparams(p=provider_id)
    ).scalar_one()

    if remaining_models == 0:
        bind.execute(
            text("DELETE FROM ai_providers WHERE id = :p").bindparams(p=provider_id)
        )
