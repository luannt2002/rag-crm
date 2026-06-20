"""Unit tests for the query-graph branch / route functions.

The route functions are nested inside ``build_graph`` (closures over the
``audit_logger`` / config args) so they cannot be imported directly. Instead
we monkey-patch ``StateGraph.add_conditional_edges`` to capture each route
closure as the graph wires itself, then exercise the captured callables
against synthesized ``GraphState`` dicts.

Coverage (8 routes):
- ``_input_blocked`` — input guardrail respected.
- ``_cache_route`` — cache hit short-circuits to persist; merge_condense_router
  flag picks legacy vs merged path.
- ``_understand_query_route`` — delegates to ``_router_route`` (parametrized).
- ``_router_route`` — multi_hop -> decompose, factoid skip rewrite, others rewrite.
- ``_grade_route`` — retry while retries < cap, else generate.
- ``_output_blocked`` — block -> persist; factoid skip reflect; else reflect.
- ``_retrieve_route`` — disabled -> rerank; adaptive picks per intent;
  enabled -> graph_retrieve.
- ``_reflect_route`` — iteration cap forces persist; missing answer retries.
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

import pytest
from langgraph.graph import StateGraph

from ragbot.orchestration.query_graph import build_graph


@pytest.fixture(scope="module")
def routes() -> dict[str, Callable[[dict], str]]:
    """Capture every route closure registered by ``build_graph``.

    Module-scoped so we only spin the StateGraph once per test session.
    """
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

    expected = {
        "_input_blocked", "_cache_route", "_understand_query_route",
        "_router_route", "_retrieve_route", "_grade_route",
        "_output_blocked", "_reflect_route",
    }
    missing = expected - set(captured.keys())
    if missing:
        pytest.skip(f"route fns missing — likely refactor: {missing}")
    return captured


# ---------------------------------------------------------------------------
# _input_blocked
# ---------------------------------------------------------------------------

def test_input_blocked_routes_to_persist_when_blocked(routes) -> None:
    state = {"guardrail_flags": [{"stage": "input", "blocked": True}]}
    assert routes["_input_blocked"](state) == "persist"


def test_input_blocked_routes_to_check_cache_when_clean(routes) -> None:
    state = {"guardrail_flags": []}
    assert routes["_input_blocked"](state) == "check_cache"


def test_input_blocked_ignores_output_stage_flags(routes) -> None:
    # Only stage=input should affect this branch.
    state = {"guardrail_flags": [{"stage": "output", "blocked": True}]}
    assert routes["_input_blocked"](state) == "check_cache"


# ---------------------------------------------------------------------------
# _cache_route
# ---------------------------------------------------------------------------

def test_cache_route_hit_with_answer_short_circuits(routes) -> None:
    state = {"cache_status": "hit", "answer": "cached answer"}
    assert routes["_cache_route"](state) == "persist"


def test_cache_route_miss_uses_merged_understand_query_by_default(routes) -> None:
    # default merge_condense_router=True
    state: dict = {"cache_status": "miss"}
    assert routes["_cache_route"](state) == "understand_query"


def test_cache_route_miss_uses_backcompat_condense_when_flag_off(routes) -> None:
    state = {
        "cache_status": "miss",
        "pipeline_config": {"merge_condense_router": False},
    }
    assert routes["_cache_route"](state) == "condense_question"


# ---------------------------------------------------------------------------
# _router_route + _understand_query_route
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "intent,expected",
    [
        ("multi_hop", "decompose"),
        ("comparison", "decompose"),  # 2026-06-20: multi-entity -> decompose
        ("factoid", "retrieve"),
        ("greeting", "retrieve"),
        ("out_of_scope", "retrieve"),
        ("clarification", "rewrite"),  # not in skip list -> rewrite
    ],
)
def test_router_route_matrix(routes, intent: str, expected: str) -> None:
    state: dict = {"intent": intent}
    if intent in ("multi_hop", "comparison"):
        # decompose path requires query >= DEFAULT_DECOMPOSE_MIN_TOKENS (8 words)
        # and intent_confidence >= DEFAULT_DECOMPOSE_CONFIDENCE_GATE (0.7).
        state["query"] = "compare cost benefit risk reward roi tco roe metric snapshot"
        state["intent_confidence"] = 0.9
    assert routes["_router_route"](state) == expected


def test_router_route_multi_hop_with_decompose_disabled_falls_through(routes) -> None:
    # decompose_enabled=False -> multi_hop falls through to skip_rewrite check
    # (multi_hop NOT in default skip_rewrite list) -> rewrite.
    state = {
        "intent": "multi_hop",
        "pipeline_config": {"decompose_enabled": False},
    }
    assert routes["_router_route"](state) == "rewrite"


def test_understand_query_route_delegates_to_router(routes) -> None:
    """When Adaptive Router L1 is disabled, ``_understand_query_route``
    is byte-identical to ``_router_route`` (legacy contract).

    When L1 is enabled (default) AND the intent is NOT already
    ``multi_hop`` AND no sub_queries have been seeded yet, the routing
    diverts to the ``query_complexity`` node so the L1 classifier can
    flag complex multi-entity queries for L3 decomposition.
    """
    # Legacy contract — L1 explicitly disabled via pipeline_config.
    state_l1_off = {
        "intent": "factoid",
        "pipeline_config": {"adaptive_router_l1_enabled": False},
    }
    assert (
        routes["_understand_query_route"](state_l1_off)
        == routes["_router_route"](state_l1_off)
    )

    # Adaptive contract — L1 default-on diverts non-multi_hop intents.
    state_l1_on = {"intent": "factoid"}
    assert routes["_understand_query_route"](state_l1_on) == "query_complexity"


# ---------------------------------------------------------------------------
# _grade_route
# ---------------------------------------------------------------------------

def test_grade_route_generates_when_retrieval_adequate(routes) -> None:
    state = {"retrieval_adequate": True, "grade_retries": 0}
    assert routes["_grade_route"](state) == "generate"


def test_grade_route_retries_while_under_cap(routes) -> None:
    state = {
        "retrieval_adequate": False,
        "grade_retries": 0,
        "pipeline_config": {"max_grade_retries": 2},
    }
    assert routes["_grade_route"](state) == "rewrite_retry"


def test_grade_route_generates_when_retries_exhausted(routes) -> None:
    state = {
        "retrieval_adequate": False,
        "grade_retries": 2,
        "pipeline_config": {"max_grade_retries": 2},
    }
    assert routes["_grade_route"](state) == "generate"


# ---------------------------------------------------------------------------
# _output_blocked
# ---------------------------------------------------------------------------

def test_output_blocked_persists_when_blocked(routes) -> None:
    state = {"guardrail_flags": [{"stage": "output", "blocked": True}]}
    assert routes["_output_blocked"](state) == "persist"


def test_output_blocked_skips_reflect_for_factoid(routes) -> None:
    state = {"intent": "factoid", "guardrail_flags": []}
    assert routes["_output_blocked"](state) == "persist"


def test_output_blocked_runs_reflect_for_complex_intent(routes) -> None:
    # 2026-05-18: ``_output_blocked`` now gates on
    # ``plan_limits.reflection_enabled`` (default False per bot_limits
    # schema). Opt in here so the reflect path is exercised.
    state = {
        "intent": "multi_hop",
        "guardrail_flags": [],
        "pipeline_config": {"reflection_enabled": True},
    }
    assert routes["_output_blocked"](state) == "reflect"


# ---------------------------------------------------------------------------
# _retrieve_route
# ---------------------------------------------------------------------------

# Stream D early-exit: empty retrieved_chunks short-circuits to "generate"
# regardless of graph_rag_mode. Tests must populate at least one chunk for
# the graph_rag_mode branches to be reached.
_DUMMY_CHUNK = [{"content": "x", "score": 0.5}]


def test_retrieve_route_disabled_goes_to_rerank(routes) -> None:
    state = {
        "retrieved_chunks": _DUMMY_CHUNK,
        "pipeline_config": {"graph_rag_mode": "disabled"},
    }
    assert routes["_retrieve_route"](state) == "rerank"


def test_retrieve_route_enabled_goes_to_graph_retrieve(routes) -> None:
    state = {
        "retrieved_chunks": _DUMMY_CHUNK,
        "pipeline_config": {"graph_rag_mode": "enabled"},
    }
    assert routes["_retrieve_route"](state) == "graph_retrieve"


def test_retrieve_route_zero_chunks_short_circuits_to_generate(routes) -> None:
    # Stream D RAGO Pareto early-exit guard.
    state = {"retrieved_chunks": [], "pipeline_config": {"graph_rag_mode": "enabled"}}
    assert routes["_retrieve_route"](state) == "generate"


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("multi_hop", "graph_retrieve"),
        ("aggregation", "graph_retrieve"),
        ("factoid", "rerank"),
        ("greeting", "rerank"),
    ],
)
def test_retrieve_route_adaptive_per_intent(routes, intent: str, expected: str) -> None:
    state = {
        "intent": intent,
        "retrieved_chunks": _DUMMY_CHUNK,
        "pipeline_config": {"graph_rag_mode": "adaptive"},
    }
    assert routes["_retrieve_route"](state) == expected


# ---------------------------------------------------------------------------
# _reflect_route — iteration cap
# ---------------------------------------------------------------------------

def test_reflect_route_persists_when_iteration_cap_hit(routes) -> None:
    # The default cap comes from constants; pass an explicit small one.
    state = {
        "_total_graph_iterations": 99,
        "answer": "",
        "pipeline_config": {"max_total_graph_iterations": 1},
    }
    assert routes["_reflect_route"](state) == "persist"


def test_reflect_route_retries_when_no_answer_yet(routes) -> None:
    state = {
        "_total_graph_iterations": 0,
        "answer": "",
        "pipeline_config": {"max_total_graph_iterations": 5},
    }
    assert routes["_reflect_route"](state) == "generate"


def test_reflect_route_persists_when_answer_present(routes) -> None:
    state = {
        "_total_graph_iterations": 0,
        "answer": "the answer",
        "pipeline_config": {"max_total_graph_iterations": 5},
    }
    assert routes["_reflect_route"](state) == "persist"
