"""S3 Pipeline-Opt: dedupe ``understand_query`` duplicate invocation.

Root cause (trace ``fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4``, step 4 + step 8):
``cache_check_and_understand_parallel`` runs ``understand_query`` upstream
when the parallel-cache flag defaults to True, merges
``_understand_skipped_by_parallel=True`` into the returned state delta, and
the graph then routes to the standalone ``understand_query`` node which
short-circuits on the marker.

Bug: ``_understand_skipped_by_parallel`` was NOT declared on the
``GraphState`` TypedDict, so LangGraph's reducer dropped the key at merge
time. Downstream node never saw the marker, body re-ran, second LLM call
burned ~1.2s per turn.

Fix: declare ``_understand_skipped_by_parallel`` + ``force_re_understand``
on GraphState; honour both in the node guard. ``force_re_understand``
restores CRAG retry's ability to request a fresh pass when needed.

These tests drive ``understand_query`` directly via ``node_callable`` so
they observe the body-skip behaviour without spinning up the whole graph.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


def _uq(compiled):
    return node_callable(compiled, "understand_query")


def test_understand_skipped_on_marker_set_no_llm_call():
    """When ``_understand_skipped_by_parallel`` is True, the body returns
    immediately — no LLM call, no step_tracker step opened, no audit
    event emitted. Asserts ``llm.complete`` was never awaited."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="factoid")
    compiled, tracker, audit, _resolver, llm = build_test_graph(
        resolver_override=resolver, llm_override=llm,
    )
    state = make_state(
        query="Điều 11 quy định gì?",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
        _understand_skipped_by_parallel=True,
    )

    out = asyncio.run(_uq(compiled)(state))

    # Body returned empty dict — no state mutation from this node.
    assert out == {}, out
    # No step_tracker step opened for understand_query (early return is
    # BEFORE the ``async with state["step_tracker"].step(...)`` line).
    assert len(tracker.by_name("understand_query")) == 0
    # No router_select_model telemetry either — both step_tracker steps
    # sit AFTER the guard.
    assert len(tracker.by_name("router_select_model")) == 0
    # LLM never invoked.
    assert llm.complete.await_count == 0
    # No audit event for intent_extracted — body short-circuited.
    assert audit.by_event("intent_extracted") == []


def test_understand_runs_when_marker_absent():
    """Fresh state without the marker → body executes normally: one
    step_tracker step, one LLM call (via the fallback path with
    structured-output disabled). This is the legacy / cache-miss-without-
    parallel-wrapper code path; the guard must not block it."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="factoid")
    compiled, tracker, _audit, _resolver, llm = build_test_graph(
        resolver_override=resolver, llm_override=llm,
    )
    state = make_state(
        query="bảo hành bao lâu",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
        # NO _understand_skipped_by_parallel key
    )

    out = asyncio.run(_uq(compiled)(state))

    # Fallback intent returned because structured-output is off.
    assert out.get("intent") == "factoid"
    # Step opened exactly once.
    assert len(tracker.by_name("understand_query")) == 1


def test_force_re_understand_overrides_marker():
    """CRAG escape hatch: ``force_re_understand=True`` makes the node run
    even when the parallel-wrapper marker says "already done". This is
    required so a rewrite_retry path can request a fresh classification
    after the first retrieve pass failed."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="factoid")
    compiled, tracker, _audit, _resolver, llm = build_test_graph(
        resolver_override=resolver, llm_override=llm,
    )
    state = make_state(
        query="câu hỏi sau retry",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
        _understand_skipped_by_parallel=True,
        force_re_understand=True,
    )

    out = asyncio.run(_uq(compiled)(state))

    # Override bypassed the guard — full body ran, intent populated again.
    assert out.get("intent") == "factoid"
    assert len(tracker.by_name("understand_query")) == 1


def test_understand_runs_once_when_invoked_twice_with_marker():
    """Simulates the production duplicate-invocation pattern: the wrapper
    runs the body upstream (sets the marker), then the graph routes to
    the standalone node. With the guard in place, the second invocation
    must be a true no-op — zero LLM calls, zero step rows added.

    This is the regression test for trace fa7983c2 step 8 (1236ms wasted
    on the duplicate LLM call)."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="factoid")
    compiled, tracker, _audit, _resolver, llm = build_test_graph(
        resolver_override=resolver, llm_override=llm,
    )

    # First invocation: no marker → body runs (mimics the parallel wrapper
    # executing the body upstream).
    state_first = make_state(
        query="Điều 11 quy định gì?",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
    )
    asyncio.run(_uq(compiled)(state_first))
    first_llm_calls = llm.complete.await_count
    first_step_count = len(tracker.by_name("understand_query"))

    # Second invocation with the marker set (simulates LangGraph routing
    # to the node after the wrapper merged the marker into state).
    state_second = make_state(
        query="Điều 11 quy định gì?",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
        _understand_skipped_by_parallel=True,
    )
    out = asyncio.run(_uq(compiled)(state_second))

    # Second call short-circuited — no additional LLM call, no new step row.
    assert out == {}
    assert llm.complete.await_count == first_llm_calls, (
        f"LLM was called again on the second invocation "
        f"(before={first_llm_calls}, after={llm.complete.await_count})"
    )
    assert len(tracker.by_name("understand_query")) == first_step_count, (
        f"Step row was added on the second invocation "
        f"(before={first_step_count}, after={len(tracker.by_name('understand_query'))})"
    )


def test_marker_declared_on_graphstate_typeddict():
    """Schema regression guard: the marker field MUST be declared on
    ``GraphState``. If a future refactor removes it, LangGraph silently
    drops the key at merge time and the duplicate-call bug returns.

    This unit test gives us a fast tripwire instead of relying on a
    full integration trace to catch the regression."""
    from ragbot.orchestration.state import GraphState

    # __annotations__ exposes the TypedDict field set including total=False
    # optional fields. Both flags must be present.
    annotations = getattr(GraphState, "__annotations__", {})
    assert "_understand_skipped_by_parallel" in annotations, (
        "GraphState lost _understand_skipped_by_parallel — LangGraph will "
        "drop the marker at merge time and understand_query will run twice."
    )
    assert "force_re_understand" in annotations, (
        "GraphState lost force_re_understand — CRAG retry can no longer "
        "request a fresh understand pass."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
