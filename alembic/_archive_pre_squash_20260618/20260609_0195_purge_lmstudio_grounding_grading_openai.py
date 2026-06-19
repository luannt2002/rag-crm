"""Purge LMStudio (self-hosted gemma) from the runtime — grounding+grading → OpenAI.

Revision: 0195
Prev:     0194

Root cause traced 2026-06-09 (request_steps, single multi-fact request = 39.4s):
grounding_check = 30.0s = exactly DEFAULT_LLM_TIMEOUT_S → the grounding LLM
(gemma-4-e2b-it on the self-hosted LMStudio endpoint llm.innocom.co) TIMES OUT on
every multi-fact turn (then degrades to skip). This is 76% of p95 latency AND
means grounding wasn't actually protecting anything on the 10 affected bots.

Drift class #2 (after the nano-drift answer-model fix, 0184): bindings point a
real-time path at a slow/unreliable self-hosted model.

Fix (config-only, no code, no-psql) — remove LMStudio from the live path:
- grounding (10 bots): gemma-4-e2b-it → gpt-4.1-nano (simple SUPPORTED/NOT
  classification — low stakes, fastest+cheapest OpenAI tier).
- grading / CRAG (10 bots): gemma-4-e2b-it → gpt-4.1-mini (decides which chunks
  reach generation — consequential, per the mini-for-grader policy).
- Disable the custom_openai (LMStudio) provider + its gemma/qwen models so nothing
  resolves to it.

Expected: p95 ~40s → ~12s; grounding actually runs (real HALLU net). A/B-gated
(load test must hold HALLU=0). Reversible.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0195"
down_revision: str | None = "0194"
branch_labels = None
depends_on = None

_REBIND = (
    ("grounding", "gpt-4.1-nano"),
    ("grading", "gpt-4.1-mini"),
)


def upgrade() -> None:
    for purpose, target in _REBIND:
        op.execute(
            text("""
                UPDATE bot_model_bindings
                SET record_model_id = (SELECT id FROM ai_models WHERE name = :tgt),
                    updated_at = NOW()
                WHERE purpose = :p
                  AND record_model_id = (SELECT id FROM ai_models WHERE name = 'gemma-4-e2b-it')
            """).bindparams(tgt=target, p=purpose)
        )
    # Take LMStudio out of resolution entirely.
    op.execute(text(
        "UPDATE ai_models SET enabled = false, updated_at = NOW() "
        "WHERE record_provider_id = (SELECT id FROM ai_providers WHERE code = 'custom_openai')"
    ))
    op.execute(text(
        "UPDATE ai_providers SET enabled = false, updated_at = NOW() WHERE code = 'custom_openai'"
    ))


def downgrade() -> None:
    op.execute(text(
        "UPDATE ai_providers SET enabled = true, updated_at = NOW() WHERE code = 'custom_openai'"
    ))
    op.execute(text(
        "UPDATE ai_models SET enabled = true, updated_at = NOW() "
        "WHERE record_provider_id = (SELECT id FROM ai_providers WHERE code = 'custom_openai')"
    ))
    for purpose, _ in _REBIND:
        op.execute(
            text("""
                UPDATE bot_model_bindings
                SET record_model_id = (SELECT id FROM ai_models WHERE name = 'gemma-4-e2b-it'),
                    updated_at = NOW()
                WHERE purpose = :p
                  AND record_model_id = (SELECT id FROM ai_models WHERE name IN ('gpt-4.1-nano','gpt-4.1-mini'))
            """).bindparams(p=purpose)
        )
