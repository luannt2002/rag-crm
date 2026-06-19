"""v0.3.0 — AI config runtime columns (ready to load live model list & keys).

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-15

Adds columns needed for loading provider/model/binding from DB into ModelResolver
cache and firing real LLM calls:

- `ai_providers`: code (slug), api_key_ref, api_key_encrypted, timeout/retry,
  max_concurrent, healthcheck_url, region, deleted_at + uniq(code).
- `ai_models`: model_id (wire id), default params, pricing (cached input),
  quality_tier, observed latency, capability flags, deleted_at +
  uniq(provider_id, model_id) + kind CHECK.
- `bot_model_bindings`: fallback_model_id, prompt_template_id,
  system_prompt_version_id, effective_from/to, deleted_at.
- `tenant_model_policy`, `bot_ai_tools`: deleted_at (+ bot_ai_tools tool_version,
  timeout_ms).
- `model_capabilities`: max_input_tokens, rate_limit_rpm/tpm, concurrency.
- `ai_config_audit_log`: index (tenant_id, resource_type, created_at DESC).

Idempotent: dùng IF NOT EXISTS / DO blocks.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "public"


def _add_col(tbl: str, ddl: str) -> None:
    op.execute(f"ALTER TABLE IF EXISTS {SCHEMA}.{tbl} ADD COLUMN IF NOT EXISTS {ddl}")


def upgrade() -> None:
    # ------------------------------------------------------------ ai_providers
    _add_col("ai_providers", "code VARCHAR(64)")
    _add_col("ai_providers", "api_key_ref VARCHAR(256)")
    _add_col("ai_providers", "api_key_encrypted TEXT")
    _add_col("ai_providers", "timeout_ms INT NOT NULL DEFAULT 30000")
    _add_col("ai_providers", "connect_timeout_ms INT NOT NULL DEFAULT 5000")
    _add_col("ai_providers", "max_retries SMALLINT NOT NULL DEFAULT 2")
    _add_col("ai_providers", "max_concurrent INT NOT NULL DEFAULT 16")
    _add_col("ai_providers", "healthcheck_url VARCHAR(512)")
    _add_col("ai_providers", "region VARCHAR(32)")
    _add_col("ai_providers", "deleted_at TIMESTAMPTZ")
    # Backfill code = slug(name) for existing rows then enforce unique.
    op.execute(
        f"UPDATE {SCHEMA}.ai_providers "
        "SET code = lower(regexp_replace(name, '[^a-zA-Z0-9]+', '_', 'g')) "
        "WHERE code IS NULL"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_providers_code "
        f"ON {SCHEMA}.ai_providers (code) WHERE deleted_at IS NULL"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_ai_providers_enabled_deleted "
        f"ON {SCHEMA}.ai_providers (enabled, deleted_at)"
    )

    # ------------------------------------------------------------ ai_models
    _add_col("ai_models", "model_id VARCHAR(128)")
    _add_col("ai_models", "input_price_per_1k_cached_usd NUMERIC(10,6)")
    _add_col("ai_models", "default_temperature NUMERIC(3,2)")
    _add_col("ai_models", "default_top_p NUMERIC(3,2)")
    _add_col("ai_models", "default_max_tokens INT")
    _add_col("ai_models", "quality_tier VARCHAR(16) NOT NULL DEFAULT 'standard'")
    _add_col("ai_models", "latency_p50_ms INT")
    _add_col("ai_models", "latency_p95_ms INT")
    _add_col("ai_models", "supports_caching BOOLEAN NOT NULL DEFAULT false")
    _add_col("ai_models", "supports_reasoning BOOLEAN NOT NULL DEFAULT false")
    _add_col("ai_models", "embedding_dimension INT")
    _add_col("ai_models", "deprecation_date DATE")
    _add_col("ai_models", "deleted_at TIMESTAMPTZ")
    op.execute(
        f"UPDATE {SCHEMA}.ai_models SET model_id = name WHERE model_id IS NULL"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_ai_models_kind'
            ) THEN
                ALTER TABLE {SCHEMA}.ai_models
                ADD CONSTRAINT ck_ai_models_kind
                CHECK (kind IN ('chat','embedding','reranker','moderation','llm'));
            END IF;
        END $$;
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_models_provider_model "
        f"ON {SCHEMA}.ai_models (provider_id, model_id) WHERE deleted_at IS NULL"
    )

    # ------------------------------------------------------- bot_model_bindings
    _add_col("bot_model_bindings", "fallback_model_id UUID")
    _add_col("bot_model_bindings", "prompt_template_id UUID")
    _add_col("bot_model_bindings", "system_prompt_version_id UUID")
    _add_col(
        "bot_model_bindings",
        "effective_from TIMESTAMPTZ NOT NULL DEFAULT now()",
    )
    _add_col("bot_model_bindings", "effective_to TIMESTAMPTZ")
    _add_col("bot_model_bindings", "deleted_at TIMESTAMPTZ")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_bindings_fallback_model'
            ) THEN
                ALTER TABLE {SCHEMA}.bot_model_bindings
                ADD CONSTRAINT fk_bindings_fallback_model
                FOREIGN KEY (fallback_model_id)
                REFERENCES {SCHEMA}.ai_models(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_bindings_bot_purpose_live "
        f"ON {SCHEMA}.bot_model_bindings (bot_id, purpose, rank, variant) "
        "WHERE deleted_at IS NULL"
    )

    # ----------------------------------------------------- tenant_model_policy
    _add_col("tenant_model_policy", "deleted_at TIMESTAMPTZ")

    # ------------------------------------------------------------- bot_ai_tools
    _add_col("bot_ai_tools", "tool_version VARCHAR(16)")
    _add_col("bot_ai_tools", "timeout_ms INT NOT NULL DEFAULT 15000")
    _add_col("bot_ai_tools", "deleted_at TIMESTAMPTZ")

    # ------------------------------------------------------- model_capabilities
    _add_col("model_capabilities", "max_input_tokens INT")
    _add_col("model_capabilities", "max_concurrent_per_key INT")
    _add_col("model_capabilities", "rate_limit_rpm INT")
    _add_col("model_capabilities", "rate_limit_tpm INT")
    _add_col(
        "model_capabilities",
        "supports_streaming BOOLEAN NOT NULL DEFAULT true",
    )

    # ------------------------------------------------------- ai_config_audit_log (may not exist on clean DB)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='{SCHEMA}' AND tablename='ai_config_audit_log') THEN
                CREATE INDEX IF NOT EXISTS ix_ai_audit_tenant_resource_time
                ON {SCHEMA}.ai_config_audit_log (tenant_id, resource_type, created_at DESC);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Minimal downgrade — drop only what we added. Data kept.
    for tbl, cols in (
        (
            "ai_providers",
            [
                "code", "api_key_ref", "api_key_encrypted",
                "timeout_ms", "connect_timeout_ms", "max_retries",
                "max_concurrent", "healthcheck_url", "region", "deleted_at",
            ],
        ),
        (
            "ai_models",
            [
                "model_id", "input_price_per_1k_cached_usd",
                "default_temperature", "default_top_p", "default_max_tokens",
                "quality_tier", "latency_p50_ms", "latency_p95_ms",
                "supports_caching", "supports_reasoning",
                "embedding_dimension", "deprecation_date", "deleted_at",
            ],
        ),
        (
            "bot_model_bindings",
            [
                "fallback_model_id", "prompt_template_id",
                "system_prompt_version_id", "effective_from", "effective_to",
                "deleted_at",
            ],
        ),
        ("tenant_model_policy", ["deleted_at"]),
        ("bot_ai_tools", ["tool_version", "timeout_ms", "deleted_at"]),
        (
            "model_capabilities",
            [
                "max_input_tokens", "max_concurrent_per_key",
                "rate_limit_rpm", "rate_limit_tpm", "supports_streaming",
            ],
        ),
    ):
        for c in cols:
            op.execute(
                f"ALTER TABLE {SCHEMA}.{tbl} DROP COLUMN IF EXISTS {c}"
            )
