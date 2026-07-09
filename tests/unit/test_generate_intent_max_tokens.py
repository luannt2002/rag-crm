"""T2-CostPerf: ``generate`` node MUST cap max_tokens by intent.

R4 verdict: ``generate`` p95 = 5.85s (2.7x the 5s SLA, dominant pipeline
latency). LLM time correlates ~linearly with output tokens, so capping
max_tokens for short intents (greeting / chitchat / off_topic / vu_vo /
hallucination_trap) shaves 30-70% generate time on those turns.

Strategy/DI: lookup table lives in ``shared/constants.py``
(``DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT``). Bot owner can override via
``pipeline_config.generate_max_tokens_by_intent`` — same shape, merged on
top at resolve time. No provider/model literal touched.

Source-level tests (no LangGraph boot required) mirror the pattern of
``test_invoke_llm_node_max_tokens.py``.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.shared.constants import DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT


# ---------------------------------------------------------------------------
# Constants-shape tests — 5 intents we explicitly target for shrink.
# ---------------------------------------------------------------------------
def test_greeting_caps_at_60() -> None:
    """v4 concision push (2026-05-01): greeting tightened 100 → 60."""
    assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT["greeting"] == 60


def test_chitchat_caps_at_80() -> None:
    """v4 concision push: chitchat tightened 150 → 80 to force 1-2 sentence
    micro-replies and stop the "Dạ ạ ... lan man" pattern from R-FRESH."""
    assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT["chitchat"] == 80


def test_factoid_caps_at_300() -> None:
    """v4 concision push: factoid tightened 450 → 300. Bot owner widens
    per-bot via pipeline_config if a domain truly needs longer cite-rich
    answers."""
    assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT["factoid"] == 300


def test_default_key_is_250() -> None:
    """v4 concision push: default 450 → 250. Unknown intent now defaults
    to a tight budget; if the classifier hands an unmapped intent the
    answer stays short instead of bullet-spamming."""
    assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT["default"] == 250


def test_short_intents_strictly_below_factoid() -> None:
    """Sanity guard: if anyone bumps a short-intent value above factoid we
    lose the perf win. V4 cleanup: dead-key labels (off_topic /
    hallucination_trap) dropped from spec — classifier never emits them."""
    factoid_cap = DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT["factoid"]
    for intent in ("greeting", "chitchat", "vu_vo", "out_of_scope"):
        assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT[intent] < factoid_cap, (
            f"intent={intent} must stay below factoid={factoid_cap} for perf shrink"
        )


def test_dead_keys_dropped_from_intent_map() -> None:
    """V4 cleanup pin: 4 dead test-label keys MUST stay out of the prod
    intent map. Re-introducing one is a regression — owner_action override
    via pipeline_config is the right place if a real producer ever lands."""
    map_keys = set(DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT.keys())
    dead_test_labels = {"off_topic", "hallucination_trap", "ambiguous", "discovery"}
    leak = map_keys & dead_test_labels
    assert not leak, f"dead test-label keys leaked back into intent map: {leak}"


# ---------------------------------------------------------------------------
# Source-level tests — generate node must wire override into both helpers.
# Mirrors ``test_invoke_llm_node_max_tokens.py`` (no DB / Redis / LiteLLM).
# ---------------------------------------------------------------------------
def test_generate_node_passes_override_to_llm_helpers() -> None:
    """``generate`` node MUST forward ``max_tokens_override=_intent_max_tokens``
    to BOTH ``_invoke_llm_node`` (free-form) and ``_invoke_structured_llm_node``
    (structured) so the cap fires regardless of which path the bot uses."""
    from ragbot.orchestration import query_graph
    from ragbot.orchestration.nodes import generate as generate_module

    # The generate node body was lifted out of build_graph into
    # orchestration/nodes/generate.py (pure relocation); scan both.
    src = inspect.getsource(query_graph) + "\n" + inspect.getsource(generate_module)
    # The per-response output cap is computed inside the generate node.
    assert "_intent_max_tokens" in src
    # Cap source moved from the DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT dict to
    # compute_output_cap(system_output_default + bot_extra_output) — a flat cap.
    # (The old per-intent dict constant is now unused; a per-intent cap, if
    # wanted, is a separate product decision.)
    assert "compute_output_cap" in src
    # Both helper invocations carry the override kwarg.
    assert "max_tokens_override=_intent_max_tokens" in src


def test_invoke_helpers_accept_max_tokens_override_kwarg() -> None:
    """Source-level guard against signature regressions on the two helpers."""
    from ragbot.orchestration import query_graph

    src = inspect.getsource(query_graph)
    # ``_invoke_llm_node`` adds the kwarg.
    assert "async def _invoke_llm_node(" in src
    # The override is min'd with cfg default, never enlarging the budget.
    assert "max_tokens_override < _max_tokens" in src
    # Structured-output sibling has the same hook.
    assert "max_tokens_override < _so_max_tokens" in src


def test_intent_max_tokens_lookup_falls_back_to_default() -> None:
    """Mirror the predicate used in the generate node: unknown intent →
    ``default`` key → 450."""
    intent_map = DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT

    def _lookup(intent: str) -> int:
        return intent_map.get(intent, intent_map["default"])

    assert _lookup("greeting") == 60
    assert _lookup("chitchat") == 80
    assert _lookup("factoid") == 300
    assert _lookup("does_not_exist_intent") == 250
    assert _lookup("") == 250


def test_intent_override_only_narrows_budget() -> None:
    """Mirror the MIN logic: override never enlarges cfg default. If the
    bot owner already set max_tokens=80, override=100 must NOT bump it."""

    def _apply(cfg_default: int | None, override: int | None) -> int | None:
        mt = cfg_default
        if override is not None and override > 0:
            if mt is None or override < mt:
                mt = int(override)
        return mt

    # Cfg None + override 100 → 100.
    assert _apply(None, 100) == 100
    # Cfg 450 + override 100 → 100 (shrunk).
    assert _apply(450, 100) == 100
    # Cfg 80 + override 100 → 80 (NOT enlarged).
    assert _apply(80, 100) == 80
    # Cfg 200 + override None → 200 (no-op).
    assert _apply(200, None) == 200
    # Cfg 200 + override 0 → 200 (zero/disabled override ignored).
    assert _apply(200, 0) == 200
