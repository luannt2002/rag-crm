"""Switch generate model gpt-4.1-mini → gpt-4.1-nano (rate-limit relief).

Revision: 0161
Prev:     0160

Trigger (operator request 2026-06-03):
  Load test of 120Q sequential hit OpenAI gpt-4.1-mini rate-limit 60s
  retry 50+ times (test rate slowed to 1.5 q/min, ETA 75 min total).
  Switch to gpt-4.1-nano which has higher rate-limit quota.

Effect:
  - All bot_model_bindings with model=gpt-4.1-mini → repoint to nano
  - Test latency expected: ~5-7s per query (vs 38s with mini retry)

Sacred-rule alignment:
  ✅ Pure alembic DML (rule 7) — no psql hot-fix
  ✅ Reversible — downgrade re-points to mini
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0161"
down_revision: str | None = "0160"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add gpt-4.1-nano model + repoint bindings from mini → nano."""
    # 1. INSERT gpt-4.1-nano row (cloned from mini)
    op.execute(text("""
        INSERT INTO ai_models (
            id, record_provider_id, name, kind, model_id,
            context_window, max_output_tokens,
            input_price_per_1k_usd, output_price_per_1k_usd,
            supports_streaming, supports_tools, supports_vision, supports_json_mode,
            supports_caching, supports_reasoning,
            languages, metadata_json, enabled,
            default_temperature, default_top_p, default_max_tokens,
            quality_tier, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            record_provider_id,
            'gpt-4.1-nano',
            kind,
            'gpt-4.1-nano',
            context_window,
            max_output_tokens,
            COALESCE(input_price_per_1k_usd, 0) * 0.4,
            COALESCE(output_price_per_1k_usd, 0) * 0.4,
            supports_streaming, supports_tools, supports_vision, supports_json_mode,
            supports_caching, supports_reasoning,
            languages,
            jsonb_build_object(
                'source', 'rate_limit_relief',
                'cloned_from', 'gpt-4.1-mini',
                'added_at', NOW()::text
            ),
            true,
            default_temperature, default_top_p, default_max_tokens,
            quality_tier,
            NOW(), NOW()
        FROM ai_models
        WHERE model_id = 'gpt-4.1-mini' AND deleted_at IS NULL
        ON CONFLICT DO NOTHING
    """))

    # 2. Update bindings: mini → nano
    op.execute(text("""
        UPDATE bot_model_bindings bmb
        SET record_model_id = (
            SELECT id FROM ai_models
            WHERE model_id = 'gpt-4.1-nano' AND deleted_at IS NULL LIMIT 1
        ),
        updated_at = NOW()
        WHERE bmb.record_model_id = (
            SELECT id FROM ai_models
            WHERE model_id = 'gpt-4.1-mini' AND deleted_at IS NULL LIMIT 1
        )
        AND bmb.active = true
    """))


def downgrade() -> None:
    """Repoint bindings back to gpt-4.1-mini + remove nano row."""
    op.execute(text("""
        UPDATE bot_model_bindings bmb
        SET record_model_id = (
            SELECT id FROM ai_models
            WHERE model_id = 'gpt-4.1-mini' AND deleted_at IS NULL LIMIT 1
        ),
        updated_at = NOW()
        WHERE bmb.record_model_id = (
            SELECT id FROM ai_models
            WHERE model_id = 'gpt-4.1-nano' AND deleted_at IS NULL LIMIT 1
        )
        AND bmb.active = true
    """))
    op.execute(text("""
        UPDATE ai_models
        SET deleted_at = NOW(), enabled = false, updated_at = NOW()
        WHERE model_id = 'gpt-4.1-nano'
    """))
