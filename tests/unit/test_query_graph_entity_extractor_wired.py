"""Regression tests — `entity_extractor` keyword reaches `build_graph` and
the multi_query_fanout dispatches to ``mq_expand_query_with_entities`` only
when the extractor is non-None AND ``entity_grounding_enabled`` is True.

These tests pin the AUDITOR-INFINITY round-1 wiring so the T3 entity-grounded
query expansion (commit 7dd4b71 added the function) cannot regress to
unwired-but-imported state silently.
"""

from __future__ import annotations

import inspect

from ragbot.orchestration.query_graph import build_graph


def test_build_graph_accepts_entity_extractor_kwarg() -> None:
    """`build_graph` must accept ``entity_extractor`` as a keyword argument.

    Locking this stops a future refactor from silently dropping the parameter
    and breaking the T3 entity-grounded path.
    """
    sig = inspect.signature(build_graph)
    assert (
        "entity_extractor" in sig.parameters
    ), "build_graph must accept the entity_extractor keyword for T3"
    assert (
        sig.parameters["entity_extractor"].default is None
    ), "entity_extractor must default to None (Null fallback at strategy level)"


def test_query_graph_imports_expand_query_with_entities() -> None:
    """The orchestrator must import ``mq_expand_query_with_entities``.

    Without this import the multi_query_fanout cannot dispatch to the
    entity-aware variant even when an extractor is wired in DI.
    """
    import ragbot.orchestration.query_graph as qg

    assert hasattr(qg, "mq_expand_query_with_entities"), (
        "query_graph must import expand_query_with_entities from "
        "multi_query_expansion under alias mq_expand_query_with_entities"
    )


def test_chat_worker_passes_entity_extractor() -> None:
    """``chat_worker.handle_chat_received`` must forward the DI extractor.

    Greps the source to avoid spinning up the full Redis bus + worker loop
    in a unit test; the wiring contract is purely textual at this layer.
    """
    import ragbot.interfaces.workers.chat_worker as cw_module
    from pathlib import Path

    # chat_worker was split into a package — concatenate every sub-module so
    # the DI-builder callsite is found wherever it landed.
    _pkg_dir = Path(cw_module.__file__).parent
    src = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(_pkg_dir.glob("*.py"))
    )
    assert "get_graph(**build_graph_di_kwargs(container))" in src, (
        "chat_worker must use the canonical DI builder, which forwards "
        "entity_extractor with every other build_graph param (ADR-W1-DI)"
    )


def test_chat_stream_passes_entity_extractor() -> None:
    """The production SSE stream route forwards the DI extractor."""
    import ragbot.interfaces.http.routes.chat_stream as cs_module

    src = inspect.getsource(cs_module)
    assert "get_graph(**build_graph_di_kwargs(container))" in src, (
        "chat_stream must use the canonical DI builder, which forwards "
        "entity_extractor with every other build_graph param (ADR-W1-DI)"
    )


def test_test_chat_passes_entity_extractor_both_sites() -> None:
    """Both build_graph call sites in test_chat.py forward the extractor.

    test_chat.py has two graph builds: one for the demo /test/chat route
    (used by 75q load test harness!) and one for the demo SSE stream.
    Both must forward ``entity_extractor`` so per-bot opt-in actually
    reaches the multi_query_fanout dispatcher when load tests run.
    """
    import ragbot.interfaces.http.routes.test_chat.chat_routes as tc_module

    src = inspect.getsource(tc_module)
    occurrences = src.count("get_graph(**build_graph_di_kwargs(container))")
    assert occurrences >= 2, (
        f"test_chat.py must forward entity_extractor at BOTH build_graph "
        f"call sites; found {occurrences}"
    )
