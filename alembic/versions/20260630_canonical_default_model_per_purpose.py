"""Canonical default-config: ONE enabled model per purpose; stop referencing dead OpenAI rows.

The platform SSoT for "which model" is ``system_config`` (``embedding_model`` →
ZeroEntropy ``zembed-1``, ``llm_default_model`` → the live answer-LLM, currently
the Innocom-gateway ``openai/claude`` row, ``reranker_model`` → ZeroEntropy
``zerank-2``). After the 2026-06-26 provider swap the live answer path was
re-pointed, but two stale references to the now-DEAD OpenAI rows survived and
make any "pick a model" path land on a 401/404 endpoint:

  1. AUX ``system_config`` LLM keys still named the dead OpenAI chat/enrich models:
     ``decomposer.model`` / ``multi_query_model`` / ``cascade_high_model`` /
     ``cascade_low_model`` / ``default_answer_model`` / ``deepeval_judge_model``
     all = ``gpt-4.1-mini``; ``enrichment_model`` = ``gpt-4.1-nano``. Any node that
     reads one of these keys drives LiteLLM at the burned OpenAI key.
  2. The dead OpenAI model rows (``gpt-4.1-mini`` / ``gpt-4.1-nano`` /
     ``text-embedding-3-small``, all provider ``openai`` @ ``api.openai.com``)
     were still ``enabled = true`` — so a "first enabled of kind" picker could
     select a dead row even though the SSoT names the live one.

This migration closes both:

  (a) Re-point every aux LLM key OFF the dead OpenAI models ONTO the live
      answer-LLM by COPYING the JSONB value of ``system_config.llm_default_model``
      (no hard-coded model name here — the live model is owned by the SSoT row,
      domain-neutral). Guarded by ``value IN (dead models)`` so a re-run is
      idempotent and only ever flips a row still pointing at a dead model.
  (b) Disable the 3 dead OpenAI rows, scoped to the OpenAI provider
      (``base_url ILIKE '%api.openai.com%' OR name = 'openai'``) so a same-named
      live row on another provider is never touched.

After upgrade each model kind has exactly ONE enabled row reachable as a default:
``zembed-1`` (embedding) and the live ``llm_default_model`` row (llm); the rerank
SSoT remains ``zerank-2``.

Content-state via tracked migration (sacred-rule 7 — never psql). Idempotent,
with a real downgrade that re-enables the OpenAI rows and restores the prior
aux-key values (guarded so it only reverses rows this migration changed).
"""
from __future__ import annotations

from alembic import op

revision = "canon_default_model_260630"
down_revision = "seed_vlm_caption_prompt_260627"
branch_labels = None
depends_on = None

# Aux system_config LLM keys that were pinned to the dead OpenAI chat model.
_AUX_KEYS_ON_DEAD_CHAT = (
    "decomposer.model",
    "multi_query_model",
    "cascade_high_model",
    "cascade_low_model",
    "default_answer_model",
    "deepeval_judge_model",
)
# Aux key pinned to the dead OpenAI enrich (nano) model.
_AUX_KEY_ON_DEAD_ENRICH = "enrichment_model"

# Dead OpenAI model rows (registry names) + their JSONB-string config values.
_DEAD_CHAT = "gpt-4.1-mini"
_DEAD_ENRICH = "gpt-4.1-nano"
_DEAD_EMBED = "text-embedding-3-small"
_DEAD_OPENAI_MODELS = (_DEAD_CHAT, _DEAD_ENRICH, _DEAD_EMBED)

# Predicate that scopes ai_models rows to the (single) OpenAI provider so a
# same-named row on another provider is never disabled.
_OPENAI_PROVIDER_PREDICATE = (
    "record_provider_id IN (SELECT id FROM ai_providers "
    "WHERE base_url ILIKE '%api.openai.com%' OR name = 'openai')"
)


def _sql_in_list(values: tuple[str, ...]) -> str:
    """Render a tuple as a SQL IN-list of single-quoted literals."""
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # (a) Re-point the 6 chat-aux keys onto the live llm_default_model by copying
    #     its JSONB value (no hard-coded model literal). value-IN guard keeps it
    #     idempotent — only rows still on the dead chat model are flipped.
    op.execute(
        f"""
        UPDATE system_config
        SET value = (SELECT value FROM system_config WHERE key = 'llm_default_model')
        WHERE key IN ({_sql_in_list(_AUX_KEYS_ON_DEAD_CHAT)})
          AND value = to_jsonb('{_DEAD_CHAT}'::text)
        """
    )
    # enrichment_model was on the dead nano model → same live answer-LLM.
    op.execute(
        f"""
        UPDATE system_config
        SET value = (SELECT value FROM system_config WHERE key = 'llm_default_model')
        WHERE key = '{_AUX_KEY_ON_DEAD_ENRICH}'
          AND value IN (to_jsonb('{_DEAD_CHAT}'::text), to_jsonb('{_DEAD_ENRICH}'::text))
        """
    )

    # (b) Disable the 3 dead OpenAI model rows (provider-scoped). Idempotent:
    #     a re-run that finds them already disabled changes nothing.
    op.execute(
        f"""
        UPDATE ai_models SET enabled = false
        WHERE name IN ({_sql_in_list(_DEAD_OPENAI_MODELS)})
          AND {_OPENAI_PROVIDER_PREDICATE}
        """
    )


def downgrade() -> None:
    # Reverse (b): re-enable the 3 dead OpenAI rows (provider-scoped).
    op.execute(
        f"""
        UPDATE ai_models SET enabled = true
        WHERE name IN ({_sql_in_list(_DEAD_OPENAI_MODELS)})
          AND {_OPENAI_PROVIDER_PREDICATE}
        """
    )

    # Reverse (a): restore the prior aux-key values. Guarded by the current live
    # value so only rows this migration changed (still equal to the live LLM)
    # are reverted — an operator's later manual override is left untouched.
    op.execute(
        f"""
        UPDATE system_config
        SET value = to_jsonb('{_DEAD_CHAT}'::text)
        WHERE key IN ({_sql_in_list(_AUX_KEYS_ON_DEAD_CHAT)})
          AND value = (SELECT value FROM system_config WHERE key = 'llm_default_model')
        """
    )
    op.execute(
        f"""
        UPDATE system_config
        SET value = to_jsonb('{_DEAD_ENRICH}'::text)
        WHERE key = '{_AUX_KEY_ON_DEAD_ENRICH}'
          AND value = (SELECT value FROM system_config WHERE key = 'llm_default_model')
        """
    )
