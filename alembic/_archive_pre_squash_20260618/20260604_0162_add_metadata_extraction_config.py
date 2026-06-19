"""Add bots.metadata_extraction_config + enable Layer 3 LLM extractor.

Revision: 0162
Prev:     0161

Trigger (Plan 260604-metadata-aware-v4):
  Layer 2 per-bot metadata extraction config — owner self-service hint
  cho Layer 3 LLM extractor. JSONB nullable: bot không config → Layer 3
  generic prompt default.

Also enables metadata_extraction_enabled = true + switch model
gpt-4.1-mini → gpt-4.1-nano (verified evidence 7/7 case 2026-06-04).

Sacred-rule alignment:
  ✅ Pure alembic DML (CLAUDE.md rule 7)
  ✅ Reversible — downgrade drop column + restore false
  ✅ Multi-tenant safe — per-bot column, isolated
  ✅ Domain-neutral — schema generic, owner declare hint qua admin
  ✅ Zero-hardcode — model name từ system_config, không từ code
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0162"
down_revision: str | None = "0161"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add bots.metadata_extraction_config + enable extraction + switch model."""

    # 1. Schema: add per-bot config column
    op.execute(text("""
        ALTER TABLE bots
        ADD COLUMN IF NOT EXISTS metadata_extraction_config JSONB DEFAULT NULL
    """))

    # 2. GIN index for JSONB containment queries
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_bots_metadata_extraction_config_gin
        ON bots USING gin (metadata_extraction_config)
        WHERE metadata_extraction_config IS NOT NULL
    """))

    # 3. Enable feature flags
    op.execute(text("""
        UPDATE system_config
        SET value = 'true', updated_at = NOW()
        WHERE key = 'metadata_extraction_enabled'
    """))
    op.execute(text("""
        UPDATE system_config
        SET value = 'true', updated_at = NOW()
        WHERE key = 'metadata_aware_retrieval_enabled'
    """))

    # 4. Switch model gpt-4.1-mini → gpt-4.1-nano
    # (verified evidence 2026-06-04: nano extract 7/7 case)
    op.execute(text("""
        UPDATE system_config
        SET value = '"gpt-4.1-nano"', updated_at = NOW()
        WHERE key = 'metadata_extraction_model'
    """))

    # 5. Seed tier ordering config (new key — operator tune without redeploy)
    op.execute(text("""
        INSERT INTO system_config (key, value, value_type, description, updated_at)
        VALUES (
            'metadata_filter_tier_order',
            CAST('["regex","per_bot","llm"]' AS jsonb),
            'json',
            'Order of metadata extraction tiers: regex (article_aware) -> per_bot (bots.metadata_extraction_config) -> llm (generic). Operator can reorder to skip layers.',
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """))


def downgrade() -> None:
    """Reverse: drop column + restore feature flags + restore model."""
    op.execute(text("""
        UPDATE system_config
        SET value = '"gpt-4.1-mini"', updated_at = NOW()
        WHERE key = 'metadata_extraction_model'
    """))
    op.execute(text("""
        UPDATE system_config
        SET value = 'false', updated_at = NOW()
        WHERE key IN ('metadata_extraction_enabled', 'metadata_aware_retrieval_enabled')
    """))
    op.execute(text("""
        DELETE FROM system_config WHERE key = 'metadata_filter_tier_order'
    """))
    op.execute(text("DROP INDEX IF EXISTS ix_bots_metadata_extraction_config_gin"))
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS metadata_extraction_config"))
