"""Route Haiku-tier system_config off the dead Anthropic key to OpenAI.

Revision: 0169
Prev:     0168

Evidence (rule #0 — no guess, curl-proven):
  ``curl api.anthropic.com`` with ``ANTHROPIC_API_KEY`` → ``authentication_error:
  invalid x-api-key``. The ``anthropic`` provider (api_key_ref=ANTHROPIC_API_KEY)
  is dead. The slot_extractor reads ``slot_extractor_model`` directly (bypassing
  the model_resolver) and resolves the alias "haiku" → anthropic/claude-haiku-4-5,
  so EVERY booking slot-extraction call failed auth → empty slots → the
  conversational lead-capture state never persisted (conversations table 0 rows).

Scope (verified by DB inspect — ``bot_model_bindings`` has ZERO anthropic rows,
so resolver-driven purposes like multi_query/grade/decompose already route to
OpenAI; only these direct-string system_config keys still name the dead model):
  - slot_extractor_model : "haiku"                     → "openai/gpt-4.1-mini"  (PROVEN live failure)
  - multi_query_model    : "haiku"                     → "gpt-4.1-mini"         (cosmetic: resolver overrides, but config must not name a dead model)
  - cascade_low_model    : "claude-haiku-4-5-20251001" → "gpt-4.1-nano"         (default-OFF; safe if a bot opts into cascade)
  - cascade_high_model   : "claude-sonnet-4-6"         → "gpt-4.1-mini"         (default-OFF; strongest active OpenAI tier)

Model choice: gpt-4.1-mini is the platform answer/decomposer/enrichment tier
(active in ai_models, supports json_mode). slot_extractor needs reliable
structured JSON, so mini over nano (nano found too weak for extraction, ref 0165).

Sacred-rule: pure alembic DML (rule 7 — config via migration, NOT psql hot-fix),
reversible, zero-hardcode (model by name, not magic id). No re-ingest needed
(query-time routing change).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0169"
down_revision: str | None = "0168"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(text("""
        UPDATE system_config SET value = '"openai/gpt-4.1-mini"', updated_at = NOW()
        WHERE key = 'slot_extractor_model';
    """))
    op.execute(text("""
        UPDATE system_config SET value = '"gpt-4.1-mini"', updated_at = NOW()
        WHERE key = 'multi_query_model';
    """))
    op.execute(text("""
        UPDATE system_config SET value = '"gpt-4.1-nano"', updated_at = NOW()
        WHERE key = 'cascade_low_model';
    """))
    op.execute(text("""
        UPDATE system_config SET value = '"gpt-4.1-mini"', updated_at = NOW()
        WHERE key = 'cascade_high_model';
    """))


def downgrade() -> None:
    op.execute(text("""
        UPDATE system_config SET value = '"haiku"', updated_at = NOW()
        WHERE key = 'slot_extractor_model';
    """))
    op.execute(text("""
        UPDATE system_config SET value = '"haiku"', updated_at = NOW()
        WHERE key = 'multi_query_model';
    """))
    op.execute(text("""
        UPDATE system_config SET value = '"claude-haiku-4-5-20251001"', updated_at = NOW()
        WHERE key = 'cascade_low_model';
    """))
    op.execute(text("""
        UPDATE system_config SET value = '"claude-sonnet-4-6"', updated_at = NOW()
        WHERE key = 'cascade_high_model';
    """))
