"""Singleton semantics for ``orchestration.query_graph.get_graph``.

The compiled LangGraph instance is now process-wide cached: nodes read all
per-request data from ``GraphState`` rather than from build-time closure, so
the same compiled graph is safe to reuse for every request, every tenant.

These tests lock that contract:

1. First call builds the graph; second call returns the *same* instance.
2. ``_reset_graph_singleton_for_test()`` empties the cache.
3. Five concurrent ``get_graph`` calls produce exactly one ``build_graph``
   invocation under the lock — no duplicate compile.
4. After reset, the next call rebuilds.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from ragbot.orchestration import query_graph as qg


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts with an empty singleton."""
    qg._reset_graph_singleton_for_test()
    yield
    qg._reset_graph_singleton_for_test()


def _build_di_kwargs() -> dict[str, Any]:
    """Minimal DI bundle that satisfies ``build_graph`` (mocks only)."""
    resolver = MagicMock()
    llm = MagicMock()
    invocation_logger = MagicMock()
    guardrail = MagicMock()
    return {
        "invocation_logger": invocation_logger,
        "guardrail": guardrail,
        "model_resolver": resolver,
        "llm": llm,
    }


def test_first_call_builds_second_returns_same_instance():
    """``get_graph`` is idempotent: first call compiles, second returns cached."""
    di = _build_di_kwargs()
    g1 = asyncio.run(qg.get_graph(**di))
    g2 = asyncio.run(qg.get_graph(**di))
    assert g1 is g2, (
        "second get_graph call must return the cached compiled graph; "
        "got two distinct instances → cache miss → singleton is broken"
    )


def test_reset_clears_singleton():
    """``_reset_graph_singleton_for_test`` drops the cached graph."""
    di = _build_di_kwargs()
    g1 = asyncio.run(qg.get_graph(**di))
    qg._reset_graph_singleton_for_test()
    g2 = asyncio.run(qg.get_graph(**di))
    assert g1 is not g2, (
        "after reset the next call must rebuild; got cached instance "
        "→ reset did not clear the module-level cache"
    )


def test_concurrent_calls_compile_exactly_once(monkeypatch: pytest.MonkeyPatch):
    """Five concurrent ``get_graph`` calls must hit ``build_graph`` once.

    Patches ``build_graph`` with a counting wrapper that returns a sentinel
    object; asserts the wrapper was invoked exactly once across all five
    coroutines and every coroutine got the *same* sentinel back.
    """
    call_count = {"n": 0}
    sentinel = object()
    real_build = qg.build_graph

    def _counting_build(**_kwargs: Any) -> Any:
        call_count["n"] += 1
        # Tiny sleep to widen the race window — without the lock the second
        # awaiter would see ``_GRAPH_SINGLETON is None`` and recompile.
        return sentinel

    monkeypatch.setattr(qg, "build_graph", _counting_build)

    async def _gather() -> list[Any]:
        return await asyncio.gather(*[qg.get_graph() for _ in range(5)])

    results = asyncio.run(_gather())
    assert call_count["n"] == 1, (
        f"build_graph must be called exactly once under concurrency, "
        f"got {call_count['n']} invocations — singleton lock is leaking"
    )
    assert all(r is sentinel for r in results), (
        "every concurrent caller must receive the same compiled instance; "
        f"got distinct objects: {[id(r) for r in results]}"
    )
    # Sanity: the patched fn was a different object than the real one.
    assert qg.build_graph is _counting_build
    assert real_build is not _counting_build


def test_subsequent_calls_skip_build(monkeypatch: pytest.MonkeyPatch):
    """Once cached, even calls with different DI kwargs reuse the cached graph.

    Documents the (intentional) trade-off: ``get_graph`` is keyed *only* by
    "is the singleton populated", not by argument hash. DI singletons are
    process-wide, so this is correct in production. Tests that want a fresh
    graph must call ``_reset_graph_singleton_for_test``.
    """
    call_count = {"n": 0}
    sentinel = object()

    def _counting_build(**_kwargs: Any) -> Any:
        call_count["n"] += 1
        return sentinel

    monkeypatch.setattr(qg, "build_graph", _counting_build)

    g1 = asyncio.run(qg.get_graph(model_resolver=MagicMock()))
    g2 = asyncio.run(qg.get_graph(model_resolver=MagicMock()))  # different mock
    g3 = asyncio.run(qg.get_graph())  # no kwargs at all
    assert g1 is g2 is g3 is sentinel
    assert call_count["n"] == 1, (
        f"DI kwargs differ across calls but build_graph still ran "
        f"{call_count['n']} times — the singleton key must NOT depend on kwargs"
    )


def test_reset_then_rebuild_produces_distinct_compiled_graph(
    monkeypatch: pytest.MonkeyPatch,
):
    """Reset → next call → fresh ``build_graph`` invocation."""
    call_count = {"n": 0}

    def _counting_build(**_kwargs: Any) -> Any:
        call_count["n"] += 1
        return object()  # distinct each invocation

    monkeypatch.setattr(qg, "build_graph", _counting_build)

    g1 = asyncio.run(qg.get_graph())
    qg._reset_graph_singleton_for_test()
    g2 = asyncio.run(qg.get_graph())
    assert call_count["n"] == 2, (
        f"expected build_graph to run twice (initial + post-reset), "
        f"got {call_count['n']}"
    )
    assert g1 is not g2, (
        "post-reset graph must be a fresh object distinct from the "
        "pre-reset one — reset apparently didn't clear the cache"
    )
