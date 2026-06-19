"""Mega-sprint G22 — drop dead LangGraph node registrations.

Background. ``build_graph`` historically registered nodes ``check_cache``
and ``rewrite`` directly on the StateGraph, then the parallel-wrapper
refactor introduced ``cache_check_and_understand_parallel`` and
``rewrite_and_mq_parallel`` as the actual node targets every conditional
edge routes to. The two original registrations became orphans — no
``add_edge`` and no conditional-route value targets the names
``check_cache`` or ``rewrite`` as graph nodes (the strings appear as
*decision keys* in conditional-edge dicts but the values map to the
parallel wrappers).

The closures themselves are still live: the parallel wrappers call them
when their config flag is OFF (byte-identical fallback). Only the
``add_node`` registrations are dead — removing them slims the compiled
graph and removes a foot-gun for future maintainers who might wire a new
edge to the orphan name.

``condense_question`` and ``router`` look superficially similar but are
genuinely reachable: ``_cache_route`` returns ``"condense_question"`` →
maps to the ``condense_question`` node when ``merge_condense_router`` is
False (legacy non-merged path), and ``condense_question`` then
``add_edge``s to ``router``. We MUST keep those two registrations.
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from ragbot.orchestration import query_graph as qg


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


def _build_minimal_graph():
    """Build the compiled LangGraph with the minimum required DI surface.

    Returns the compiled graph object. Real LLM / vector store / embedder
    are not invoked during compile — LangGraph only walks ``add_node`` /
    ``add_edge`` calls — so passing ``None`` for the optional ports is
    sufficient. ``llm`` and ``model_resolver`` are required kwargs but
    never invoked at compile time.
    """
    resolver = MagicMock()
    llm = MagicMock()
    return qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        llm=llm,
        model_resolver=resolver,
    )


# --------------------------------------------------------------------------- #
# 1. Compiled-graph node-set assertion — the load-bearing contract.           #
# --------------------------------------------------------------------------- #


def test_compiled_graph_omits_dead_check_cache_node() -> None:
    """``check_cache`` must NOT be a node in the compiled graph.

    Pre-fix: ``graph.add_node("check_cache", check_cache)`` registered
    a node name that no edge ever targets. Compile would still wire it
    (LangGraph allows orphan nodes), but the orphan is dead in every
    request flow.

    Post-fix: only the parallel wrapper ``cache_check_and_understand_parallel``
    remains as the real node — the closure ``check_cache`` is still
    invoked by the wrapper when its flag is OFF.
    """
    compiled = _build_minimal_graph()
    nodes = set(compiled.nodes.keys())
    assert "check_cache" not in nodes, (
        "Dead node ``check_cache`` re-introduced — no conditional edge "
        "targets this name; the closure is still invoked by the parallel "
        "wrapper. Compiled nodes: " + repr(sorted(nodes))
    )
    # Sanity — the parallel wrapper that replaced it must still be there.
    assert "cache_check_and_understand_parallel" in nodes, (
        "Parallel wrapper missing — would break cache check entirely. "
        "Compiled nodes: " + repr(sorted(nodes))
    )


def test_compiled_graph_omits_dead_rewrite_node() -> None:
    """``rewrite`` must NOT be a node in the compiled graph.

    Pre-fix: ``graph.add_node("rewrite", rewrite)`` + a stray
    ``add_edge("rewrite", "retrieve")`` registered a node that no
    conditional route targets — every router that decides to "rewrite"
    routes to ``rewrite_and_mq_parallel`` instead.
    """
    compiled = _build_minimal_graph()
    nodes = set(compiled.nodes.keys())
    assert "rewrite" not in nodes, (
        "Dead node ``rewrite`` re-introduced — every conditional route "
        "with key ``rewrite`` maps to ``rewrite_and_mq_parallel``. The "
        "closure is still invoked by the parallel wrapper. Compiled "
        "nodes: " + repr(sorted(nodes))
    )
    assert "rewrite_and_mq_parallel" in nodes, (
        "Parallel wrapper missing — would break the rewrite branch "
        "entirely. Compiled nodes: " + repr(sorted(nodes))
    )


# --------------------------------------------------------------------------- #
# 2. Live nodes that LOOK dead must remain.                                   #
# --------------------------------------------------------------------------- #


def test_compiled_graph_keeps_baseline_condense_question_node() -> None:
    """``condense_question`` is reachable via ``_cache_route`` when
    ``merge_condense_router`` is False (non-merged baseline path). The
    config flag defaults True today but bot owners may flip it; removing
    the node would silently break the baseline path.
    """
    compiled = _build_minimal_graph()
    nodes = set(compiled.nodes.keys())
    assert "condense_question" in nodes, (
        "Baseline node ``condense_question`` removed — bot owners with "
        "``pipeline_merge_condense_router=False`` would hit a missing-"
        "node compile error. Compiled nodes: " + repr(sorted(nodes))
    )


def test_compiled_graph_keeps_baseline_router_node() -> None:
    """``router`` is reachable via the explicit edge
    ``add_edge("condense_question", "router")``. Removing it would
    orphan ``condense_question`` (its only successor).
    """
    compiled = _build_minimal_graph()
    nodes = set(compiled.nodes.keys())
    assert "router" in nodes, (
        "Legacy node ``router`` removed — ``condense_question`` would "
        "lose its only successor. Compiled nodes: " + repr(sorted(nodes))
    )


# --------------------------------------------------------------------------- #
# 3. Source-level guard — the registrations themselves.                       #
# --------------------------------------------------------------------------- #


def test_no_orphan_check_cache_add_node_call() -> None:
    """Source-level pin — protects against accidental re-add via copy/
    paste of the legacy registration block."""
    src = inspect.getsource(qg.build_graph)
    assert 'graph.add_node("check_cache", check_cache)' not in src, (
        "Orphan registration ``add_node(\"check_cache\", check_cache)`` "
        "re-introduced — no conditional edge targets ``check_cache`` as "
        "a node name; the closure is invoked by the parallel wrapper."
    )


def test_no_orphan_rewrite_add_node_call() -> None:
    """Source-level pin — protects against accidental re-add via copy/
    paste of the legacy registration block."""
    src = inspect.getsource(qg.build_graph)
    assert 'graph.add_node("rewrite", rewrite)' not in src, (
        "Orphan registration ``add_node(\"rewrite\", rewrite)`` "
        "re-introduced — every router decision with key ``rewrite`` "
        "maps to ``rewrite_and_mq_parallel`` instead."
    )


def test_no_orphan_rewrite_to_retrieve_edge() -> None:
    """The ``add_edge("rewrite", "retrieve")`` line is also dead —
    its source node is the orphan registration removed above. Keeping
    the edge would cause LangGraph to raise on compile (edge from a
    nonexistent node)."""
    src = inspect.getsource(qg.build_graph)
    assert 'graph.add_edge("rewrite", "retrieve")' not in src, (
        "Dead edge ``add_edge(\"rewrite\", \"retrieve\")`` re-introduced "
        "— would break compile after the ``rewrite`` node registration "
        "is gone (LangGraph rejects edges from unknown source nodes)."
    )


# --------------------------------------------------------------------------- #
# 4. Closures themselves remain — they are still called by parallel wrappers. #
# --------------------------------------------------------------------------- #


def test_check_cache_closure_still_defined_in_build_graph() -> None:
    """The ``check_cache`` callable is invoked by
    ``cache_check_and_understand_parallel`` when its flag is OFF. The
    binding MUST stay in build_graph so the fallback ``check_cache(state)``
    call resolves; only its node registration is removed.

    check_cache's body now lives in nodes/check_cache.py and build_graph
    binds it via functools.partial — still callable as ``check_cache(state)``
    (the partial supplies the di_kwargs), so the byte-identical fallback
    path is preserved.
    """
    src = inspect.getsource(qg.build_graph)
    assert "check_cache = functools.partial(" in src, (
        "Binding ``check_cache`` removed but the parallel wrapper still "
        "calls it for the byte-identical fallback path."
    )
    assert "check_cache(state)" in src, (
        "The parallel wrapper's fallback call to check_cache(state) is gone."
    )


def test_rewrite_closure_still_defined_in_build_graph() -> None:
    """The ``rewrite`` callable is invoked by ``rewrite_and_mq_parallel``
    when its flag is OFF. The binding MUST stay so the fallback
    ``rewrite(state)`` call resolves.

    rewrite's body now lives in nodes/rewrite.py; build_graph binds it via
    functools.partial — still callable as ``rewrite(state)``.
    """
    src = inspect.getsource(qg.build_graph)
    assert "rewrite = functools.partial(" in src, (
        "Closure ``rewrite`` removed but the parallel wrapper still "
        "calls it for the byte-identical fallback path."
    )
