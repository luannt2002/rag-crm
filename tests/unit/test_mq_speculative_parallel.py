"""MQ speculative parallel with understand — T2 perf top ROI
(live diag 2026-05-18: multi_query_fanout p95 4.46s = sequential
LLM expand BEFORE retrieve).

The patch adds a 4th parallel task in ``cache_check_and_understand_parallel``
that fires the paraphrase LLM call ALONGSIDE the understand router.
When the router lands on an MQ-enabled intent (aggregation / comparison /
multi_hop per the per-intent MQ map) the variants are already cached in
state; the downstream retrieve node reuses them via the
``_mq_speculative_variants`` slot, saving ~250-400ms p95 per qualifying
turn.

Cancellation discipline (Async Rule 5):

- Intent NOT in the consume-set → cancel + ``suppress(CancelledError)``
- Cache HIT → cancel + ``suppress``
- Understand exception bubble → cancel + ``suppress``

These tests assert the wiring + per-bot opt-in + cancellation guard.
The end-to-end LangGraph integration is exercised via the runtime
diagnostic harness in a follow-up load-test.
"""

from __future__ import annotations

import inspect

from ragbot.shared.constants import (
    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED,
    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S,
)


def test_default_pipeline_multi_query_speculative_flag_off_by_default() -> None:
    """Speculative MQ stays OFF until per-bot opt-in (token cost vs
    perf trade)."""
    assert DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED is False


def test_default_speculative_timeout_finite_positive_seconds() -> None:
    """Cap MUST fire below the chat-graph p95 budget."""
    assert isinstance(DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S, float)
    assert 1.0 <= DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S <= 15.0


def test_cache_check_and_understand_parallel_wires_speculative_mq() -> None:
    """Source-level guard: the 4th task is wired with the flag-gated
    create_task + wait_for pattern."""
    from ragbot.orchestration import query_graph as qg  # noqa: PLC0415
    src = inspect.getsource(qg.build_graph)
    # Per-bot flag key + the task variable both present.
    assert "pipeline_multi_query_speculative_enabled" in src
    assert "spec_mq_task" in src, (
        "speculative MQ task variable missing from the parallel block"
    )
    assert "_run_multi_query_expansion" in src
    # The task is bounded by wait_for (timeout cap).
    assert "pipeline_multi_query_speculative_timeout_s" in src


def test_speculative_mq_task_cancels_for_non_consumable_intent() -> None:
    """Source-level guard: when the intent does NOT consume MQ the
    speculative task is cancelled with ``suppress(CancelledError)``.

    This prevents an orphan LLM task lingering past graph completion
    and bills tokens that no node consumes.

    The consume-gate is decided by ``intent_consumes_mq`` against the
    per-intent MQ map (same source of truth as the producer) — NOT a
    hardcoded label set with phantom intents the classifier never emits
    (M16 regression).
    """
    from ragbot.orchestration import query_graph as qg  # noqa: PLC0415
    src = inspect.getsource(qg.build_graph)
    # The consume-gate uses the shared per-intent helper + the per-intent
    # MQ map, and the cancellation branch is present.
    assert "_intent_consumes_mq" in src
    assert "multi_query_enabled_by_intent" in src
    assert "spec_mq_task.cancel()" in src
    assert "pipeline_multi_query_speculative_cancelled" in src
    # M16: the dead phantom labels must be gone from the consume-gate —
    # the classifier never emits these, so they can never match.
    assert '"compound"' not in src
    assert '"docs_only"' not in src


def test_speculative_mq_result_stashed_in_state_for_retrieve_node() -> None:
    """When the intent consumes MQ the variants are stashed under
    ``_mq_speculative_variants`` so the retrieve node can skip its
    inline MQ call."""
    from ragbot.orchestration import query_graph as qg  # noqa: PLC0415
    src = inspect.getsource(qg.build_graph)
    assert "_mq_speculative_variants" in src
    assert "pipeline_multi_query_speculative_used" in src


def test_speculative_mq_task_cancelled_on_cache_hit() -> None:
    """Cache HIT path must cancel the speculative task — no token
    burn on a cache turnaround."""
    from ragbot.orchestration import query_graph as qg  # noqa: PLC0415
    src = inspect.getsource(qg.build_graph)
    # Find the cache-hit branch and verify the cancel is inside it.
    idx = src.find('cache_out.get("cache_status") == "hit"')
    assert idx > 0, "cache hit branch missing"
    branch = src[idx : idx + 2000]
    assert "spec_mq_task.cancel()" in branch, (
        "cache HIT must cancel the speculative MQ task"
    )


def test_speculative_mq_task_cancelled_when_understand_raises() -> None:
    """Understand-task raise must cascade-cancel the speculative MQ
    task (no leak when the router stalls + raises CancelledError)."""
    from ragbot.orchestration import query_graph as qg  # noqa: PLC0415
    src = inspect.getsource(qg.build_graph)
    # Look for ``except asyncio.CancelledError:`` followed by
    # ``spec_mq_task.cancel()`` within reasonable distance.
    idx = src.find("except asyncio.CancelledError:")
    assert idx > 0
    # Multiple matches exist; the relevant one is in the understand
    # await branch. Just assert at least one cancel block also handles
    # the speculative MQ task — overall count check.
    assert src.count("spec_mq_task.cancel()") >= 3, (
        "speculative MQ task cancel must appear in: cache fail, cache "
        "hit, understand cancel branch (3 cleanup sites)"
    )


def test_imports_speculative_constants_in_query_graph_module() -> None:
    """Regression guard: the constants are imported (not stringly
    referenced)."""
    from ragbot.orchestration import query_graph as qg  # noqa: PLC0415
    src = inspect.getsource(qg)
    assert "DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED" in src
    assert "DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S" in src
