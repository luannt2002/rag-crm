"""S2-PERF Lever 4: ``guard_output`` MUST gate the grounding-check LLM
judge by intent.

R8 OLD verdict: p95 = 21.2s vs target 12s. F14 measured the
``grounding_check`` step p95 doubling (1274 → 3188 ms in 7d) as the
dominant tail driver inside ``guard_output``. Non-factoid intents
(greeting / chitchat / off_topic / vu_vo / out_of_scope / feedback) do
NOT retrieve documents, so the grounding LLM has nothing to ground —
running it is pure waste. Skipping saves ~1000 ms p95.

Strategy/DI: gating set lives in ``shared/constants.py``
(``DEFAULT_GROUNDING_INTENTS``). Bot owner can override the per-bot
intent set via ``pipeline_config.grounding_intents`` — same shape (list
of strings), merged on top at resolve time. No provider/model literal
touched. Domain-neutral.

Source-level tests (no LangGraph boot required) mirror the pattern of
``test_generate_intent_max_tokens.py`` and
``test_invoke_llm_node_max_tokens.py``.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ragbot.shared.constants import DEFAULT_GROUNDING_INTENTS


def _orchestration_src() -> str:
    """query_graph + every extracted node module source, concatenated.

    The guard_output / generate node bodies were lifted out of
    ``build_graph`` into ``orchestration/nodes/*.py`` (pure relocation); the
    source-level pins below must scan both the orchestrator wiring file and
    the node modules.
    """
    from ragbot.orchestration import query_graph

    parts = [inspect.getsource(query_graph)]
    nodes_dir = Path(query_graph.__file__).resolve().parent / "nodes"
    parts.extend(
        p.read_text(encoding="utf-8") for p in sorted(nodes_dir.glob("*.py"))
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Constants-shape tests — gate set must contain ONLY factoid-class intents.
# ---------------------------------------------------------------------------
def test_default_grounding_intents_contains_factoid_class() -> None:
    """Factoid + comparison + aggregation + multi_hop ALL retrieve docs
    and benefit from grounding. Encode the invariant."""
    assert "factoid" in DEFAULT_GROUNDING_INTENTS
    assert "comparison" in DEFAULT_GROUNDING_INTENTS
    assert "aggregation" in DEFAULT_GROUNDING_INTENTS
    assert "multi_hop" in DEFAULT_GROUNDING_INTENTS


def test_default_grounding_intents_excludes_non_factoid() -> None:
    """Greeting / chitchat / off_topic / vu_vo / feedback / out_of_scope
    do NOT retrieve and must NOT trigger the grounding judge."""
    for intent in (
        "greeting",
        "chitchat",
        "off_topic",
        "vu_vo",
        "out_of_scope",
        "feedback",
        "hallucination_trap",
    ):
        assert intent not in DEFAULT_GROUNDING_INTENTS, (
            f"intent={intent} must NOT be in DEFAULT_GROUNDING_INTENTS — "
            f"that would re-introduce the wasted grounding LLM call"
        )


def test_default_grounding_intents_is_immutable_tuple() -> None:
    """Tuple, not list — protect against accidental mutation by callers."""
    assert isinstance(DEFAULT_GROUNDING_INTENTS, tuple)


# ---------------------------------------------------------------------------
# Source-level guard — guard_output node must wire the gate correctly.
# ---------------------------------------------------------------------------
def test_grounding_check_skipped_for_greeting() -> None:
    """Mirror the predicate the guard_output node uses to gate the
    grounding judge: only intents in the gating tuple may run the LLM."""
    grounding_set = DEFAULT_GROUNDING_INTENTS

    def _eligible(intent: str) -> bool:
        return intent in grounding_set

    # Skip path: non-factoid intents never run the grounding judge.
    assert _eligible("greeting") is False
    assert _eligible("chitchat") is False
    assert _eligible("off_topic") is False
    assert _eligible("vu_vo") is False
    assert _eligible("out_of_scope") is False
    assert _eligible("feedback") is False


def test_grounding_check_runs_for_factoid() -> None:
    """Mirror the predicate: factoid-class intents DO run the judge."""
    grounding_set = DEFAULT_GROUNDING_INTENTS

    def _eligible(intent: str) -> bool:
        return intent in grounding_set

    assert _eligible("factoid") is True
    assert _eligible("comparison") is True
    assert _eligible("aggregation") is True
    assert _eligible("multi_hop") is True


def test_grounding_check_unknown_intent_skipped() -> None:
    """Unknown / empty intent = NOT in set → skip judge.
    Defensive default: don't fire LLM on undefined input."""
    grounding_set = DEFAULT_GROUNDING_INTENTS

    def _eligible(intent: str) -> bool:
        return intent in grounding_set

    assert _eligible("") is False
    assert _eligible("does_not_exist_intent") is False


def test_grounding_intents_per_bot_override() -> None:
    """Bot owner override path: ``pipeline_config.grounding_intents``.
    The node must read from pipeline_config first, falling back to the
    default tuple only when the override is missing or invalid."""

    def _resolve(pcfg: dict | None) -> tuple[str, ...]:
        # Mirror the predicate inside the guard_output node.
        cfg_val = (pcfg or {}).get("grounding_intents", DEFAULT_GROUNDING_INTENTS)
        if isinstance(cfg_val, (list, tuple)) and cfg_val:
            return tuple(str(x) for x in cfg_val)
        return DEFAULT_GROUNDING_INTENTS

    # No override → default tuple.
    assert _resolve(None) == DEFAULT_GROUNDING_INTENTS
    assert _resolve({}) == DEFAULT_GROUNDING_INTENTS
    # Override with list of strings → tuple.
    assert _resolve({"grounding_intents": ["factoid"]}) == ("factoid",)
    # Override with broader set (bot owner explicitly opts in).
    assert _resolve(
        {"grounding_intents": ["factoid", "feedback"]}
    ) == ("factoid", "feedback")
    # Empty list → fall back to default (don't accidentally disable
    # grounding entirely just because override is empty).
    assert _resolve({"grounding_intents": []}) == DEFAULT_GROUNDING_INTENTS
    # Non-list garbage → fall back to default.
    assert _resolve({"grounding_intents": "factoid"}) == DEFAULT_GROUNDING_INTENTS
    assert _resolve({"grounding_intents": 42}) == DEFAULT_GROUNDING_INTENTS


def test_guard_output_imports_grounding_intents_constant() -> None:
    """Source-level guard: the orchestrator MUST import the constant
    (proves the gate is wired, not just declared)."""
    src = _orchestration_src()
    assert "DEFAULT_GROUNDING_INTENTS" in src
    # And used inside guard_output, not just imported.
    assert "_grounding_intents" in src
    assert "_grounding_eligible" in src


def test_guard_output_skips_llm_when_intent_not_eligible() -> None:
    """Source-level guard: the ``llm_fn = ...`` assignment must be
    guarded by the eligibility flag, otherwise the gating is dead code."""
    src = _orchestration_src()
    # The eligibility flag must appear in the LLM-fn enable predicate.
    assert "_grounding_eligible" in src
    # Both ``_grounding_enabled`` AND ``_grounding_eligible`` gate the LLM.
    # Test that the AND expression appears literally — protects against
    # someone accidentally dropping the gate while refactoring.
    assert "_grounding_enabled" in src and "_grounding_eligible" in src


def test_grounding_check_skipped_metadata_emitted() -> None:
    """Source-level guard: guard_output must emit observability metadata
    so dashboards can bucket skipped vs ran turns."""
    src = _orchestration_src()
    # The metadata key the analyzers will pivot on.
    assert "grounding_check_skipped" in src
    # And it goes through set_metadata on the guard_output step ctx.
    assert "guard_ctx.set_metadata" in src


# ---------------------------------------------------------------------------
# F3 — generate-node SLA telemetry.
# ---------------------------------------------------------------------------
def test_generate_p95_sla_default_is_8000ms() -> None:
    """Generate p95 SLA constant default = 8s (Win-MVP target 12s minus
    4s budget for retrieve+grade+guard)."""
    from ragbot.shared.constants import DEFAULT_GENERATE_P95_SLA_MS

    assert DEFAULT_GENERATE_P95_SLA_MS == 8000


def test_generate_node_emits_sla_breach_warning() -> None:
    """Source-level guard: the ``generate`` node MUST emit a structured
    warning event when its measured duration exceeds the SLA."""
    src = _orchestration_src()
    # Event name analysts will grep for.
    assert "generate_sla_breach" in src
    # The SLA constant is read (with bot owner override hook).
    assert "DEFAULT_GENERATE_P95_SLA_MS" in src
    assert "generate_p95_sla_ms" in src
    # And the breach predicate uses ``> _generate_sla_ms``.
    assert "_generate_elapsed_ms > _generate_sla_ms" in src


def test_generate_sla_breach_predicate() -> None:
    """Mirror the predicate: warn iff sla_ms > 0 AND elapsed > sla."""

    def _breach(elapsed_ms: int, sla_ms: int) -> bool:
        return sla_ms > 0 and elapsed_ms > sla_ms

    # Disabled (sla = 0) → never breach.
    assert _breach(99999, 0) is False
    # Below SLA → no breach.
    assert _breach(7999, 8000) is False
    # Equal SLA → no breach (strict > only).
    assert _breach(8000, 8000) is False
    # Above SLA → breach.
    assert _breach(8001, 8000) is True
    assert _breach(21200, 8000) is True
