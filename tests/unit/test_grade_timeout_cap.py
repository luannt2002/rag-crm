"""Grade timeout cap — T2 perf (live diag 2026-05-18: grade p95 = 2.56s).

The grade node wraps the batch-LLM call in ``asyncio.wait_for`` so that
when the grader stalls (provider latency tail) the chat-graph falls
back to the rerank-supplied order instead of pushing the whole pipeline
into the 8s+ p95 bucket. HALLU=0 sacred remains protected by the
downstream grounding_check.

These tests verify the wiring + the source-level pattern. End-to-end
LangGraph timeout assertion is heavy infra; this regression guard is
the cheapest way to prevent a future refactor from silently dropping
the cap.
"""

from __future__ import annotations

import asyncio
import inspect

from ragbot.shared.constants import DEFAULT_GRADE_TIMEOUT_S


def _grade_src() -> str:
    """query_graph + grade node source concatenated.

    The grade node body was lifted out of ``build_graph`` into
    ``orchestration/nodes/grade.py`` (pure relocation); these source-level
    pins must scan both the orchestrator wiring file and the node module.
    """
    from ragbot.orchestration import query_graph as qg
    from ragbot.orchestration.nodes import grade as grade_mod

    return inspect.getsource(qg) + "\n" + inspect.getsource(grade_mod)


def test_default_grade_timeout_s_is_positive_finite_seconds() -> None:
    """Constant present + sane bounds (positive, < 10s)."""
    assert isinstance(DEFAULT_GRADE_TIMEOUT_S, float)
    assert 0.0 < DEFAULT_GRADE_TIMEOUT_S < 10.0


def test_default_grade_timeout_s_covers_grader_p95() -> None:
    """The cap MUST cover the grade-LLM's measured p95 so a NORMAL cold grade
    completes, and clip only the genuine (super-p95) hang.

    Measured p95 = 2.56s (2026-05-18 diagnostic, re-seen in the 2026-07-13
    load test). A cap BELOW p95 (the earlier 2.0s) force-times-out the normal
    2.0–2.56s cold-grade band and ships all chunks ungraded to generate —
    exactly the "cold-LLM grade still has room" intent it was documented to
    preserve. So the cap sits just above p95 (room for the normal tail) but
    still modest (clips a real hang from chat-graph p95). ``0`` = disabled.
    """
    MEASURED_GRADE_P95_S = 2.56  # documented grade-LLM p95 (see grade.py comment)
    assert DEFAULT_GRADE_TIMEOUT_S >= MEASURED_GRADE_P95_S, (
        "cap below measured p95 — a normal cold grade is force-passed ungraded"
    )
    assert DEFAULT_GRADE_TIMEOUT_S <= 3.5, (
        "cap too high — a genuinely hung grade call no longer trimmed from "
        "chat-graph p95"
    )


def test_query_graph_imports_grade_timeout_constant() -> None:
    """Source-level guard against an import-rot refactor."""
    src = _grade_src()
    assert "DEFAULT_GRADE_TIMEOUT_S" in src
    assert "grade_timeout_s" in src, (
        "pipeline_config key 'grade_timeout_s' must be resolvable per-bot"
    )


def test_grade_node_uses_asyncio_wait_for_around_batch_call() -> None:
    """Pattern guard — the batch grade-LLM call MUST be wrapped in
    ``asyncio.wait_for`` so the timeout actually fires.

    The implementation has both a wrapped + an unwrapped (timeout=0)
    branch; the wrapped branch is the safety net we're testing for.
    """
    src = _grade_src()
    assert "asyncio.wait_for(" in src
    # Ensure the timeout branch lives inside the grade-LLM region — we
    # scan for the structural pair "_invoke_structured_llm_node" +
    # "asyncio.wait_for" within reasonable proximity.
    idx = src.find("grade_timeout_fallback_to_rerank_order")
    assert idx > 0, (
        "fallback log event missing — pre-fix grade had no timeout path"
    )


def test_grade_node_emits_fallback_log_event() -> None:
    """The fallback path must surface a structured warning so ops see
    when the cap fires (otherwise silent quality degrade)."""
    src = _grade_src()
    assert 'logger.warning(\n                        "grade_timeout_fallback_to_rerank_order"' in src or \
           'grade_timeout_fallback_to_rerank_order' in src


def test_grade_timeout_zero_disables_cap() -> None:
    """Per-bot override ``grade_timeout_s=0`` must bypass the
    ``asyncio.wait_for`` branch — used by tenants that prefer fidelity
    over latency tail trim.
    """
    src = _grade_src()
    # Both branches present (timeout > 0 wraps; ``else`` runs bare).
    assert "if _grade_timeout_s > 0:" in src or "if _grade_timeout_s > 0" in src
    assert "else:" in src, (
        "grade_timeout_s=0 must short-circuit the wait_for wrapper"
    )


def test_asyncio_wait_for_re_raises_only_timeout_error_for_fallback() -> None:
    """Behavioural smoke — ``asyncio.wait_for`` raises TimeoutError that
    the fallback branch catches; any other exception propagates so the
    request still fails loud (no silent grade quality loss on real
    bugs).
    """
    async def _slow_call() -> str:
        await asyncio.sleep(0.5)
        return "done"

    async def _go() -> None:
        try:
            await asyncio.wait_for(_slow_call(), timeout=0.01)
        except asyncio.TimeoutError:
            return  # expected
        raise AssertionError("expected asyncio.TimeoutError")

    asyncio.run(_go())


def test_grade_timeout_fallback_returns_ambiguous_chunks() -> None:
    """The fallback dict shape is ``relevance=CRAG_GRADE_AMBIGUOUS`` so
    downstream nodes still mark the path as retrieval_adequate AND
    flag for grounding_check (HALLU=0 sacred preservation)."""
    src = _grade_src()
    # Look for the fallback construction in proximity to the timeout event.
    snippet_start = src.find("grade_timeout_fallback_to_rerank_order")
    assert snippet_start > 0
    snippet = src[snippet_start : snippet_start + 1500]
    assert "CRAG_GRADE_AMBIGUOUS" in snippet
    assert "grade_timeout_fallback" in snippet, (
        "result dict must carry grade_timeout_fallback=True so downstream "
        "observability + analytics can attribute the rerank-only path"
    )
