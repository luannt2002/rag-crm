"""Revive grounding/slot/metadata LLM calls → innocom (raw-model-name calls hit dead OpenAI).

Phase 0 / S0-B. Empty-answer root: several LLM call-sites resolve their model from
``system_config`` RAW MODEL NAMES (``slot_extractor_model='openai/gpt-4.1-mini'``,
``llm_default_model``, ``metadata_extraction_model``, ``contextual_retrieval_model``)
instead of a provider-scoped binding — so they bypass the api_base/api_key resolution
and litellm dispatches them to the (burned-429) real OpenAI endpoint, failing the whole
turn (empty answer on slot/booking + grounding-bound nano on legal).

OpenAI is fully dead (chat+embed 429); innocom is the live OpenAI-compatible gateway.
Fix (config-state, sacred #7 — never psql):
 1. Re-point the ``grounding`` + ``enrichment`` bindings off the dead ``gpt-4.1-nano``
    row → the innocom ``openai/claude`` model row (so query-path grounding + ingest
    enrichment reach a live model).
 2. Point the raw ``system_config`` model-name keys at the innocom wire name
    ``openai/claude``. Combined with ``OPENAI_API_BASE``/``OPENAI_API_KEY`` env now
    pointing at innocom (.env), these raw ``openai/`` calls dispatch to innocom.

Domain-neutral: keyed on purpose/model-row, covers all N bots. NOTE: routing raw
model-names is itself an anti-pattern (S0-C will move structured calls to capability
routing); this migration is the immediate revive.
"""
from __future__ import annotations

from alembic import op

revision = "revive_grounding_slot_260626"
down_revision = "embed_swap_ze1280_260626"
branch_labels = None
depends_on = None

_INNOCOM_MODEL_ID = "a1b2c3d4-0000-4000-8000-000000000c02"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = '{_INNOCOM_MODEL_ID}'
        WHERE purpose IN ('grounding', 'enrichment')
          AND record_model_id IN (SELECT id FROM ai_models WHERE name = 'gpt-4.1-nano')
        """,
    )
    op.execute(
        """
        UPDATE system_config SET value = '"openai/claude"'
        WHERE key IN ('llm_default_model', 'slot_extractor_model',
                      'metadata_extraction_model', 'contextual_retrieval_model')
        """,
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = (SELECT id FROM ai_models WHERE name = 'gpt-4.1-nano')
        WHERE purpose IN ('grounding', 'enrichment')
          AND record_model_id = '{_INNOCOM_MODEL_ID}'
        """,
    )
    op.execute("UPDATE system_config SET value = '\"gpt-4.1-mini\"' WHERE key = 'llm_default_model'")
    op.execute("UPDATE system_config SET value = '\"openai/gpt-4.1-mini\"' WHERE key = 'slot_extractor_model'")
    op.execute("UPDATE system_config SET value = '\"gpt-4.1-mini\"' WHERE key = 'metadata_extraction_model'")
    op.execute("UPDATE system_config SET value = '\"gpt-4.1-nano\"' WHERE key = 'contextual_retrieval_model'")
