"""Grounding async flag verification — T2 perf (flag SHIP ONLY).

Background (Q9 in ``RAGBOT_21_QUESTIONS_ANSWERS.md``): grounding judge
runs ~100% of qualifying turns, contributing 1.31s p95. The async path
schedules the judge as a fire-and-forget ``asyncio.create_task`` AFTER
the user response is on the wire — moving the latency off the request
critical path.

These tests verify the existing async code path is reachable and emits
the correct event when the flag is enabled. The default flag value
STAYS OFF (HALLU=0 sacred; Auditor-Chief decides default ON after the
90Q load-test trap gate passes).
"""

from __future__ import annotations

import asyncio
import inspect

from ragbot.shared.constants import (
    DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
    DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS,
    DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
)


def _build_graph_and_guard_src() -> str:
    """build_graph wiring + guard_output node source concatenated.

    The guard_output node body was lifted out of ``build_graph`` into
    ``orchestration/nodes/guard_output.py`` (pure relocation); the async-gate
    pins below assert on that node body, so scan both.
    """
    from ragbot.orchestration import query_graph as qg
    from ragbot.orchestration.nodes import guard_output as guard_mod

    return inspect.getsource(qg.build_graph) + "\n" + inspect.getsource(guard_mod)


def test_grounding_async_default_off_until_load_test_gate_passes() -> None:
    """Default MUST stay False — HALLU=0 sacred. Auditor-Chief flips
    after 90Q load-test trap gate."""
    assert DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED is False, (
        "default ON would bypass the HALLU=0 sync gate — Auditor-Chief "
        "must validate via 90Q trap load-test before flipping"
    )


def test_grounding_async_default_intents_include_factoid_only() -> None:
    """Async eligible intent set starts with factoid (lowest HALLU risk —
    chunk-grounded numeric / fact answers)."""
    assert "factoid" in DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS
    # Synthesis + multi_hop deliberately excluded (higher HALLU risk in
    # the async path; need separate load-test gate per intent).


def test_grounding_async_top_score_floor_non_trivial() -> None:
    """Top-score floor keeps async-path turns to the high-confidence
    retrieval bucket; low-score turns stay sync (worst-case judge
    must run before response ships)."""
    assert 0.5 <= DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD <= 1.0


def test_schedule_grounding_check_background_uses_asyncio_create_task() -> None:
    """The scheduler uses ``asyncio.create_task`` — verifies the async
    path is non-blocking on the response critical path."""
    from ragbot.orchestration.query_graph import (
        _schedule_grounding_check_background,
    )
    src = inspect.getsource(_schedule_grounding_check_background)
    assert "asyncio.create_task" in src, (
        "async grounding path must schedule via create_task — otherwise "
        "the judge still blocks the response wire"
    )
    # Defensive: no-loop branch must close the coroutine (no
    # 'coroutine was never awaited' warning).
    assert "coro.close()" in src


def test_guard_output_invokes_async_scheduler_when_flag_true() -> None:
    """Source-level: when the 4-gate eligibility passes the scheduler
    is called and the sync ``llm_fn`` is left None (so the sync judge
    does not run)."""
    src = _build_graph_and_guard_src()
    # The eligibility-block sets _grounding_async then the
    # post-guards-out branch dispatches the scheduler.
    assert "_schedule_grounding_check_background" in src
    assert "if _grounding_async:" in src
    # And the sync judge is suppressed when async fires.
    assert "and not _grounding_async" in src, (
        "llm_fn assignment must include the ``not _grounding_async`` "
        "guard so the sync path is suppressed when async is eligible"
    )


def test_async_path_observable_via_step_metadata() -> None:
    """The guard_output step metadata records ``grounding_check_async``
    so analytics + ops dashboards can attribute the off-critical-path
    latency."""
    src = _build_graph_and_guard_src()
    assert "grounding_check_async=" in src
    assert "grounding_check_async_top_score=" in src


def test_async_eligibility_requires_all_four_gates() -> None:
    """The eligibility ``and`` chain MUST gate on all four conditions
    so a single misconfig (intent / floor / flag / no-LLM) defaults to
    the sync path. HALLU=0 sacred — async is the optimisation, sync is
    the safety net.
    """
    src = _build_graph_and_guard_src()
    # Locate the _grounding_async = bool(...) assignment block and
    # count the ``and`` operators inside it.
    idx = src.find("_grounding_async = bool(")
    assert idx > 0
    block = src[idx : idx + 1000]
    assert block.count(" and ") >= 5, (
        "eligibility check must compose at least 5 ANDed conditions: "
        "grounding_enabled, eligible, async_enabled_cfg, intent in set, "
        "top_score >= floor, model_resolver, llm"
    )


def test_async_scheduler_stashes_task_on_state_for_graceful_shutdown() -> None:
    """``state['grounding_async_task']`` MUST hold the Task so a
    graceful-shutdown hook can await pending judges before draining."""
    from ragbot.orchestration.query_graph import (
        _schedule_grounding_check_background,
    )
    src = inspect.getsource(_schedule_grounding_check_background)
    assert 'state["grounding_async_task"] = task' in src


def test_async_scheduler_runtime_smoke_returns_task_when_loop_present() -> None:
    """Smoke test the scheduler under a running loop — it must return
    a Task instance + stash it on state."""
    from ragbot.orchestration.query_graph import (
        _schedule_grounding_check_background,
    )

    async def _go() -> None:
        state: dict = {
            "answer": "",
            "graded_chunks": [],
            "reranked_chunks": [],
        }
        # No LLM call actually happens — the resolver is None, the
        # inner coroutine will short-circuit. We only want the
        # Task creation + state stash assertion.
        task = _schedule_grounding_check_background(
            state=state,
            threshold=0.7,
            top_score=0.9,
            model_resolver=None,
            llm=None,
        )
        assert task is not None
        assert "grounding_async_task" in state
        # Drain the task so pytest does not warn on un-awaited tasks.
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, BaseException):
            pass

    asyncio.run(_go())
