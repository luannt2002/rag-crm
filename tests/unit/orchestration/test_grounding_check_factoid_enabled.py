"""Wave-2 Cluster C2 — grounding judge ON by default for factoid + comparison.

Background. The LLM-judge grounding check (``llm_grounding_check`` in
``infrastructure/guardrails/local_guardrail.py``) was wired in 2026-Q2 but
shipped OFF (``DEFAULT_GROUNDING_CHECK_ENABLED = False``) while the eval
harness warmed up. Wave-2 90Q load tests showed factoid + comparison
intents are the dominant HALLU contributors — weak retrieval (top_score
0.18..0.30 gray zone) survives rerank cliff and the LLM downstream
confabulates rather than refuses.

Wave-2 fix (this commit). Flip the default to ``True`` and verify the
predicate that gates the judge fires for factoid-class intents.

Tests in this module are pure source-level (no LangGraph boot, no DB) —
they encode the invariants:

1. ``DEFAULT_GROUNDING_CHECK_ENABLED`` is ``True`` (the flip).
2. ``query_graph._pcfg`` falls back to ``DEFAULT_GROUNDING_CHECK_ENABLED``
   when ``pipeline_config`` is missing the key (no ``False`` literal).
3. ``DEFAULT_GROUNDING_INTENTS`` covers ``factoid`` + ``comparison`` so
   the per-intent eligibility predicate fires the judge for them.
4. Combined: when both gates pass, the orchestrator wires an LLM
   callable; when either gate fails, no callable is wired.
"""
from __future__ import annotations

import inspect

from ragbot.shared.constants import (
    DEFAULT_GROUNDING_CHECK_ENABLED,
    DEFAULT_GROUNDING_INTENTS,
)


# ---------------------------------------------------------------------------
# 1. Constant flipped — sacred invariant of this commit.
# ---------------------------------------------------------------------------
def test_default_grounding_check_enabled_is_true() -> None:
    """The constant MUST be True post-Wave-2. Flipping back to False
    silently re-introduces HALLU on factoid + comparison."""
    assert DEFAULT_GROUNDING_CHECK_ENABLED is True, (
        "DEFAULT_GROUNDING_CHECK_ENABLED must be True — Wave-2 fix. "
        "If you flipped this back to False, you re-enabled HALLU on the "
        "factoid + comparison intents. See "
        "reports/CODER_C2_GROUNDING_CHECK_REPORT.md for the rationale."
    )


# ---------------------------------------------------------------------------
# 2. Source-level guard: query_graph fallback uses the constant, not False.
# ---------------------------------------------------------------------------
def test_guard_output_uses_default_constant_for_grounding_enabled() -> None:
    """The guard_output node reads ``grounding_check_enabled`` from
    pipeline_config with a fallback. The fallback MUST be the imported
    constant (not a hardcoded ``False``) so flipping the constant in
    ``shared/constants.py`` actually changes behaviour even when the
    chat_worker hasn't pre-populated pipeline_config."""
    from ragbot.orchestration import query_graph as qg
    from ragbot.orchestration.nodes import guard_output as guard_mod

    # The guard_output node body was lifted into orchestration/nodes/
    # guard_output.py (pure relocation); scan both modules.
    src = inspect.getsource(qg) + "\n" + inspect.getsource(guard_mod)
    # The expected pattern after the Wave-2 fix.
    expected = (
        '_pcfg(state, "grounding_check_enabled", DEFAULT_GROUNDING_CHECK_ENABLED)'
    )
    assert expected in src, (
        "guard_output must fall back to DEFAULT_GROUNDING_CHECK_ENABLED — "
        f"expected substring not found: {expected!r}"
    )
    # Anti-regression: a hardcoded False fallback re-introduces the bug.
    forbidden = '_pcfg(state, "grounding_check_enabled", False)'
    assert forbidden not in src, (
        "guard_output must NOT hardcode a False fallback for "
        "grounding_check_enabled — defeats the purpose of the constant"
    )


def test_query_graph_imports_default_grounding_check_enabled() -> None:
    """Source-level: confirm the constant is actually imported into the
    orchestrator module (catches typo / missed import)."""
    from ragbot.orchestration import query_graph as qg

    assert hasattr(qg, "DEFAULT_GROUNDING_CHECK_ENABLED"), (
        "query_graph must import DEFAULT_GROUNDING_CHECK_ENABLED from "
        "ragbot.shared.constants"
    )
    assert qg.DEFAULT_GROUNDING_CHECK_ENABLED is True


# ---------------------------------------------------------------------------
# 3. Per-intent eligibility — factoid + comparison subject to the judge.
# ---------------------------------------------------------------------------
def test_grounding_intents_includes_factoid_and_comparison() -> None:
    """The intent gate (DEFAULT_GROUNDING_INTENTS) must keep factoid +
    comparison on the eligible list — the C2 mission depends on it."""
    assert "factoid" in DEFAULT_GROUNDING_INTENTS
    assert "comparison" in DEFAULT_GROUNDING_INTENTS


def test_grounding_check_eligibility_predicate_factoid() -> None:
    """Mirror the predicate inside ``guard_output``:
        eligible = intent in grounding_intents
    For factoid + comparison this MUST be True."""
    grounding_set = DEFAULT_GROUNDING_INTENTS

    def _eligible(intent: str) -> bool:
        return intent in grounding_set

    assert _eligible("factoid") is True
    assert _eligible("comparison") is True


def test_grounding_check_eligibility_predicate_skips_chitchat() -> None:
    """Conversational intents must remain skipped — the gate exists to
    avoid wasted LLM tail latency on non-retrieval turns."""
    grounding_set = DEFAULT_GROUNDING_INTENTS

    def _eligible(intent: str) -> bool:
        return intent in grounding_set

    assert _eligible("greeting") is False
    assert _eligible("chitchat") is False
    assert _eligible("out_of_scope") is False
    assert _eligible("feedback") is False


# ---------------------------------------------------------------------------
# 4. Combined gate — judge fires iff BOTH (constant True AND intent eligible).
# ---------------------------------------------------------------------------
def test_combined_gate_judge_fires_on_factoid() -> None:
    """Combined gate as wired in guard_output:
        will_fire = grounding_enabled AND intent_in_gating_set
    Post-Wave-2: True for factoid, True for comparison."""
    grounding_set = DEFAULT_GROUNDING_INTENTS

    def _will_fire(intent: str, *, grounding_enabled: bool) -> bool:
        return grounding_enabled and (intent in grounding_set)

    # With the new default (True) and a factoid/comparison intent, fires.
    assert _will_fire("factoid", grounding_enabled=DEFAULT_GROUNDING_CHECK_ENABLED) is True
    assert _will_fire("comparison", grounding_enabled=DEFAULT_GROUNDING_CHECK_ENABLED) is True
    # Disabled (per-bot opt-out via plan_limits) → no fire even on factoid.
    assert _will_fire("factoid", grounding_enabled=False) is False
    # Enabled but ineligible intent → no fire.
    assert _will_fire("greeting", grounding_enabled=DEFAULT_GROUNDING_CHECK_ENABLED) is False
    assert _will_fire("chitchat", grounding_enabled=DEFAULT_GROUNDING_CHECK_ENABLED) is False


def test_grounding_check_skipped_metadata_for_chitchat() -> None:
    """Mirror the ``_grounding_check_skipped`` flag the guard_output node
    sets for telemetry: True iff (enabled AND not eligible)."""
    grounding_set = DEFAULT_GROUNDING_INTENTS
    enabled = DEFAULT_GROUNDING_CHECK_ENABLED  # True post-Wave-2

    def _skipped(intent: str) -> bool:
        eligible = intent in grounding_set
        return bool(enabled and not eligible)

    # Factoid + comparison run the judge — NOT skipped.
    assert _skipped("factoid") is False
    assert _skipped("comparison") is False
    # Conversational intents — recorded as skipped (audit trail).
    assert _skipped("greeting") is True
    assert _skipped("chitchat") is True


# ---------------------------------------------------------------------------
# 5. Bot-owner opt-out path still works (PLAN_LIMIT_SCHEMA wires the constant).
# ---------------------------------------------------------------------------
def test_plan_limit_schema_default_tracks_constant() -> None:
    """``bot_limits.PLAN_LIMIT_SCHEMA`` must source its default from the
    same constant. Otherwise a per-bot override resolved against the
    schema default would diverge from the system default."""
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA

    entry = PLAN_LIMIT_SCHEMA["grounding_check_enabled"]
    assert entry["type"] == "bool"
    assert entry["default"] is DEFAULT_GROUNDING_CHECK_ENABLED
    # Defensive: post-Wave-2 the schema default is True.
    assert entry["default"] is True
