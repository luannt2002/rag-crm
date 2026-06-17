"""[T2-CostPerf] Bug #2 — ``_output_blocked`` respects ``reflection_enabled``.

Production evidence (req ``9cf611b5``): the reflect node fired twice on
bot ``test-spa-id`` (1571ms + 1997ms = 3.57s wasted) despite
``plan_limits.reflection_enabled = None`` (defaults to False per
``shared/bot_limits.py``). SQL confirms 0 bots in the platform set the
flag to true.

Root cause: ``_output_blocked`` only short-circuits to ``persist`` when
(a) an output-stage guardrail flag is blocked OR (b) the intent is in
``skip_reflect_intents``. The ``reflection_enabled`` plan_limits knob is
NOT consulted, so every non-skip intent burns one extra LLM call.

Surgical fix: insert a ``reflection_enabled`` gate BEFORE the intent
check. When the flag is False (the universe-wide default today), route
straight to ``persist`` — saving ~2-4s on every non-trivial turn.

Test contract (drives the route closures the same way
``test_query_graph_route_functions.py`` does — capture them via the
``StateGraph.add_conditional_edges`` monkey-patch).
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

import pytest
from langgraph.graph import StateGraph

from ragbot.orchestration.query_graph import build_graph


@pytest.fixture(scope="module")
def routes() -> dict[str, Callable[[dict], str]]:
    """Capture every route closure registered by ``build_graph``."""
    captured: dict[str, Callable[[dict], str]] = {}
    original = StateGraph.add_conditional_edges

    def _hook(self, source, path, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = getattr(path, "__name__", None)
        if name:
            captured[name] = path
        return original(self, source, path, *args, **kwargs)

    StateGraph.add_conditional_edges = _hook  # type: ignore[method-assign]
    try:
        build_graph(
            invocation_logger=MagicMock(),
            guardrail=MagicMock(),
            llm=MagicMock(),
            model_resolver=MagicMock(),
        )
    finally:
        StateGraph.add_conditional_edges = original  # type: ignore[method-assign]

    if "_output_blocked" not in captured:
        pytest.skip("_output_blocked closure missing — refactored away")
    return captured


# ---------------------------------------------------------------------------
# Gate semantics
# ---------------------------------------------------------------------------

def test_output_blocked_persists_when_reflection_disabled_default(routes) -> None:
    """Default ``reflection_enabled = False`` (per bot_limits schema) →
    a non-skip intent (multi_hop / synthesis) must route to ``persist``,
    not ``reflect``. Saves the 1.5-2s reflect-node round-trip per turn.
    """
    state = {
        "intent": "multi_hop",
        "guardrail_flags": [],
        "pipeline_config": {"reflection_enabled": False},
    }
    assert routes["_output_blocked"](state) == "persist"


def test_output_blocked_persists_when_pipeline_config_missing(routes) -> None:
    """Defensive: when ``pipeline_config`` is unset (legacy / partial
    state), the gate falls back to ``DEFAULT_REFLECTION_ENABLED`` which
    is False — same persist outcome."""
    state = {"intent": "multi_hop", "guardrail_flags": []}
    assert routes["_output_blocked"](state) == "persist"


def test_output_blocked_reflects_when_reflection_enabled_true(routes) -> None:
    """When a bot owner opts in (``plan_limits.reflection_enabled = True``)
    the reflect node MUST fire for non-skip intents — backward-compatible
    with bots that paid for the smart-skip retry."""
    state = {
        "intent": "multi_hop",
        "guardrail_flags": [],
        "pipeline_config": {"reflection_enabled": True},
    }
    assert routes["_output_blocked"](state) == "reflect"


def test_output_blocked_factoid_still_skips_when_flag_on(routes) -> None:
    """Even with reflection ON, intents in ``skip_reflect_intents``
    (factoid / greeting / chitchat / OOS / feedback / vu_vo) must continue
    to bypass reflect — that pre-existing skip-list behaviour is preserved.
    """
    state = {
        "intent": "factoid",
        "guardrail_flags": [],
        "pipeline_config": {"reflection_enabled": True},
    }
    assert routes["_output_blocked"](state) == "persist"


def test_output_blocked_output_guardrail_blocked_short_circuits(routes) -> None:
    """When the output-stage guardrail flagged the answer, the route
    ALWAYS persists — regardless of the reflection_enabled gate. This
    invariant prevents reflect from re-running on a blocked answer."""
    state = {
        "intent": "multi_hop",
        "guardrail_flags": [{"stage": "output", "blocked": True}],
        "pipeline_config": {"reflection_enabled": True},
    }
    assert routes["_output_blocked"](state) == "persist"


def test_output_blocked_skip_intent_overrides_with_flag_off(routes) -> None:
    """Belt-and-braces: with both reflection_enabled=False AND a
    skip-list intent, the route still persists (covers the legacy
    bots that never opt in)."""
    state = {
        "intent": "greeting",
        "guardrail_flags": [],
        "pipeline_config": {"reflection_enabled": False},
    }
    assert routes["_output_blocked"](state) == "persist"
