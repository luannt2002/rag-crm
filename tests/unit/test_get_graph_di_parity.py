"""Transport DI parity for ``get_graph`` callsites (ADR-W1-DI).

``get_graph`` is first-caller-wins by design (see
``test_build_graph_singleton.py`` — that contract stays). The hazard is the
*callsites*: each transport hand-rolls its own kwargs list, so whichever
builds first decides which optional deps exist for the whole process. These
tests pin the fix:

1. Build-order reproduction — a stream-shaped first build must still supply
   the worker-only deps (``hyde_generator`` / ``understand_query_cache`` /
   ``stats_index_repo`` / ``doc_repo``), i.e. every production callsite goes
   through one shared assembly function.
2. AST parity — every ``get_graph(...)`` production callsite either forwards
   the canonical builder (``**build_graph_di_kwargs(...)``) or names the
   full ``build_graph`` parameter set explicitly. Drift = RED forever.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from ragbot.orchestration import query_graph as qg

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "ragbot"

# Production get_graph callsites (worker + SSE + demo sync + demo stream).
_CALLSITE_FILES = [
    # chat_worker was split into a package; the get_graph callsite lives in
    # the pipeline sub-module.
    _SRC / "interfaces" / "workers" / "chat_worker" / "pipeline.py",
    _SRC / "interfaces" / "http" / "routes" / "chat_stream.py",
    _SRC / "interfaces" / "http" / "routes" / "test_chat" / "chat_routes.py",
]

# The canonical builder every callsite must use (or match param-for-param).
_BUILDER_NAME = "build_graph_di_kwargs"

# Deps chat_stream historically dropped (P2-K 🐛-K1).
_WORKER_ONLY_DEPS = (
    "understand_query_cache",
    "hyde_generator",
    "stats_index_repo",
    "doc_repo",
)


def _build_graph_param_names() -> frozenset[str]:
    """Full keyword-parameter set of ``build_graph`` — the parity target."""
    sig = inspect.signature(qg.build_graph)
    return frozenset(sig.parameters)


@pytest.fixture(autouse=True)
def _reset_singleton():
    qg._reset_graph_singleton_for_test()
    yield
    qg._reset_graph_singleton_for_test()


# --------------------------------------------------------------------------
# V1 — build-order reproduction
# --------------------------------------------------------------------------

def _container_with_all_providers() -> MagicMock:
    """Container double whose every provider attr returns a MagicMock."""
    container = MagicMock()
    return container


def test_stream_first_build_supplies_worker_only_deps(
    monkeypatch: pytest.MonkeyPatch,
):
    """First build via the shared builder carries ALL 24 deps.

    Before ADR-W1-DI the SSE callsite passed only 20 kwargs, so an SSE-first
    warm-up silently disabled HyDE / uq-cache / stats / parent-child for the
    whole process. The shared builder makes the build-order irrelevant.
    """
    from ragbot.orchestration.graph_assembly import build_graph_di_kwargs

    captured: dict[str, Any] = {}

    def _capturing_build(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    # Snapshot the REAL signature + assemble kwargs first; only then patch
    # build_graph (both the builder and the helper introspect the live fn).
    full_params = _build_graph_param_names()
    kwargs = build_graph_di_kwargs(_container_with_all_providers())
    monkeypatch.setattr(qg, "build_graph", _capturing_build)
    asyncio.run(qg.get_graph(**kwargs))

    missing = [d for d in _WORKER_ONLY_DEPS if d not in captured]
    assert not missing, (
        f"first build dropped worker-only deps {missing} — SSE-first warm-up "
        "would disable them process-wide (P2-K 🐛-K1)"
    )
    assert frozenset(captured) == full_params, (
        "shared builder must emit exactly the build_graph parameter set; "
        f"diff: +{sorted(frozenset(captured) - full_params)} "
        f"-{sorted(full_params - frozenset(captured))}"
    )


def test_singleton_ignore_kwargs_semantics_unchanged(
    monkeypatch: pytest.MonkeyPatch,
):
    """Lock-in: first-caller-wins stays exactly as documented (đừng đụng)."""
    call_count = {"n": 0}
    sentinel = object()

    def _counting_build(**_kwargs: Any) -> Any:
        call_count["n"] += 1
        return sentinel

    monkeypatch.setattr(qg, "build_graph", _counting_build)

    g1 = asyncio.run(qg.get_graph(llm=MagicMock()))
    g2 = asyncio.run(qg.get_graph(llm=MagicMock(), model_resolver=MagicMock()))
    assert g1 is g2 is sentinel
    assert call_count["n"] == 1


# --------------------------------------------------------------------------
# V2 — AST kwarg-set parity (permanent drift guard)
# --------------------------------------------------------------------------

def _iter_get_graph_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name == "get_graph":
            yield node


def _call_uses_canonical_builder(call: ast.Call) -> bool:
    """True when the call is ``get_graph(**build_graph_di_kwargs(...))``."""
    for kw in call.keywords:
        if kw.arg is None:  # **expansion
            value = kw.value
            if isinstance(value, ast.Call):
                fn = value.func
                name = (
                    fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                )
                if name == _BUILDER_NAME:
                    return True
    return False


def test_every_production_callsite_has_full_kwarg_parity():
    """Each production ``get_graph(...)`` call forwards the canonical builder
    or names every ``build_graph`` parameter explicitly.

    This is the regression guard that makes kwarg drift impossible to
    reintroduce silently (root cause §2.5 of ADR-W1-DI: four hand-rolled
    copies diverged the day a new DI param was added to only one of them).
    """
    full = _build_graph_param_names()
    failures: list[str] = []

    for path in _CALLSITE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = list(_iter_get_graph_calls(tree))
        assert calls, f"expected at least one get_graph call in {path.name}"
        for call in calls:
            if _call_uses_canonical_builder(call):
                continue
            named = frozenset(kw.arg for kw in call.keywords if kw.arg)
            missing = sorted(full - named)
            if missing:
                failures.append(
                    f"{path.name}:{call.lineno} missing {missing}"
                )

    assert not failures, (
        "get_graph callsites drifted from build_graph parameter set "
        f"(use **{_BUILDER_NAME}(container)):\n  " + "\n  ".join(failures)
    )
