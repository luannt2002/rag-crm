"""Pin tests — canonical default-config: ONE enabled model per purpose + deterministic seeder.

Two guards in this file:

1. The alembic migration ``canonical_default_model_per_purpose_260630`` must
   (a) re-point every AUX system_config LLM key OFF the dead OpenAI models
   (gpt-4.1-mini / gpt-4.1-nano) ONTO the live answer-LLM (the value of
   ``llm_default_model``), and (b) disable the 3 dead OpenAI model rows
   (gpt-4.1-mini / gpt-4.1-nano / text-embedding-3-small) scoped to the OpenAI
   provider. It must chain off the current head, be idempotent (only touch rows
   currently on the dead models), and have a real downgrade.

2. The bot-create seeder's last-resort "first enabled of kind" fallback query
   must be deterministic — i.e. carry an ``ORDER BY`` so the heap-order pick is
   reproducible across DB clones. (Source-inspection guard, mirrors
   ``test_stats_query_attributes_selected.py``.)
"""

from __future__ import annotations

import inspect
from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260630_canonical_default_model_per_purpose.py"
)

# The AUX system_config LLM keys that pointed at the dead OpenAI models and must
# be re-pointed onto the live answer-LLM (``llm_default_model``).
_AUX_LLM_KEYS = (
    "decomposer.model",
    "multi_query_model",
    "cascade_high_model",
    "cascade_low_model",
    "default_answer_model",
    "enrichment_model",
    "deepeval_judge_model",
)

# The 3 dead OpenAI model rows the migration disables.
_DEAD_OPENAI_MODELS = (
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "text-embedding-3-small",
)


def _read() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Part 1 — migration                                                          #
# --------------------------------------------------------------------------- #
def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing migration: {_MIGRATION_PATH}"


def test_revision_chains_off_current_head() -> None:
    """Must chain off the verified current head seed_vlm_caption_prompt_260627."""
    src = _read()
    assert 'revision = "canonical_default_model_per_purpose_260630"' in src
    assert 'down_revision = "seed_vlm_caption_prompt_260627"' in src


def test_upgrade_repoints_every_aux_llm_key() -> None:
    """Each aux key must appear in an UPDATE that re-points it to the live LLM."""
    src = _read()
    # The whole upgrade re-points keys to the value of llm_default_model — the
    # subquery that reads it must be present (no hard-coded model literal).
    assert "FROM system_config WHERE key = 'llm_default_model'" in src, (
        "aux keys must be re-pointed to the live llm_default_model value, "
        "read from system_config (not a hard-coded model name)"
    )
    for key in _AUX_LLM_KEYS:
        assert key in src, f"aux key {key!r} not referenced in migration"


def test_upgrade_is_idempotent_only_touches_dead_models() -> None:
    """Re-pointing must be guarded by the row's current value so a re-run touches
    ONLY rows still on a dead OpenAI model (idempotent)."""
    src = _read()
    # The dead model names must appear in the value guards.
    assert "gpt-4.1-mini" in src
    assert "gpt-4.1-nano" in src
    # An idempotency guard on the value column (only flip rows currently dead).
    assert "value IN" in src or "value =" in src, (
        "aux re-point must be guarded by the current value so re-runs are idempotent"
    )
    # No hard-coded live-model literal — re-point copies the SSoT value.
    assert "SET value = (SELECT value FROM system_config WHERE key = 'llm_default_model')" in src


def test_upgrade_disables_three_dead_openai_models() -> None:
    """Disable the 3 dead OpenAI rows, scoped to the OpenAI provider."""
    src = _read()
    assert "UPDATE ai_models SET enabled = false" in src
    for model in _DEAD_OPENAI_MODELS:
        assert model in src, f"dead model {model!r} not disabled in migration"
    # Provider scoping so we never disable a same-named live row on another provider.
    assert "api.openai.com" in src and "ai_providers" in src, (
        "disable must be scoped to the OpenAI provider (base_url / name='openai')"
    )


def test_downgrade_reverses_both_parts() -> None:
    """Downgrade re-enables the 3 dead rows AND restores the prior aux values."""
    src = _read()
    lower = src.lower()
    assert "def downgrade" in src
    # re-enable
    assert "set enabled = true" in lower
    # restore aux keys to their prior dead-model values
    assert "gpt-4.1-mini" in src and "gpt-4.1-nano" in src


def test_no_version_ref_or_bot_literal() -> None:
    """Domain-neutral + no-version-ref: no bot slug, no v1/v2/legacy tokens."""
    src = _read()
    lowered = src.lower()
    for tok in ("_v1", "_v2", "_v3", "_legacy", "_old", "_new"):
        assert tok not in lowered, f"version-ref token {tok!r} leaked into migration"


# --------------------------------------------------------------------------- #
# Part 2 — deterministic seeder fallback                                       #
# --------------------------------------------------------------------------- #
def test_seeder_fallback_query_is_deterministic() -> None:
    """The bot-create model-resolution scan must ORDER BY so the last-resort
    'first enabled of kind' pick is reproducible (not heap order)."""
    from ragbot.interfaces.http.routes.test_chat import bot_admin_routes

    src = inspect.getsource(bot_admin_routes.create_bot)
    # The enabled-of-kind scan that feeds _first_of_kind must be ordered.
    assert "WHERE enabled = true AND kind IN ('llm', 'embedding')" in src, (
        "the model-resolution scan moved — re-point this guard"
    )
    assert "ORDER BY created_at" in src, (
        "the first-enabled-of-kind fallback scan must ORDER BY created_at so the "
        "deterministic last-resort pick is reproducible across DB clones"
    )
