"""Regression tests for mega-sprint-G11 (Issue 11 NameError prevent).

The orchestrator at ``query_graph.guard_output`` calls
``_schedule_grounding_check_background(...)`` whenever the bot owner has
flipped ``plan_limits.grounding_check_async_enabled`` on AND retrieval
top-score clears the floor. Before this fix the function was undefined,
so the very first request that satisfied the gate raised NameError
inside the worker — silently degrading the entire pipeline.

These tests are scoped to the three contracts called out in the G11
prompt:

  1. ``_schedule_grounding_check_background`` is defined and callable.
  2. When invoked outside a running event loop it degrades silent
     (no exception bubbles up to the caller).
  3. When the gate fires inside a running event loop, it actually
     drives the injected grounding judge (``llm.complete``) — confirming
     the function does not no-op the judge into oblivion.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from ragbot.orchestration import query_graph as qg


# ---------------------------------------------------------------------------
# Stubs — mirror the lightweight harness used by the broader
# test_grounding_async_background suite, but kept self-contained so this
# regression file can run in isolation.
# ---------------------------------------------------------------------------
class _StubResolver:
    async def resolve_runtime(self, **_: Any) -> Any:
        @dataclass
        class _Cfg:
            model_name: str = "stub"
            litellm_name: str = "stub/stub-v1"
        return _Cfg()


class _RecordingLLM:
    """LLM stub that records every ``complete`` invocation. The text body
    matches the regex parser fallback inside ``llm_grounding_check`` so
    the judge follows the SUPPORTED branch end-to-end."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, cfg: Any, *, messages: list[dict], **kwargs: Any) -> dict:
        self.calls.append({"cfg": cfg, "messages": messages, **kwargs})
        body = "1. SUPPORTED\n2. SUPPORTED"
        return {"text": body, "finish_reason": "stop", "usage": {}}


def _state(answer: str = "Sentence one. Sentence two.") -> dict:
    return {
        "answer": answer,
        "graded_chunks": [
            {"chunk_id": "c1", "content": "Supporting fact A.", "score": 0.85},
            {"chunk_id": "c2", "content": "Supporting fact B.", "score": 0.81},
        ],
        "record_tenant_id": "tenant-uuid",
        "record_bot_id": "bot-uuid",
        "request_id": "req-uuid",
        "message_id": 12345,
    }


# ---------------------------------------------------------------------------
# 1. Symbol-level: function is defined.
# ---------------------------------------------------------------------------
def test_schedule_grounding_check_background_defined() -> None:
    """The orchestrator's call site references this name unprefixed; if
    the symbol disappears again the NameError surfaces here long before
    it lands in production."""
    fn = getattr(qg, "_schedule_grounding_check_background", None)
    assert fn is not None, (
        "_schedule_grounding_check_background MUST be defined at module "
        "scope of ragbot.orchestration.query_graph — guard_output's call "
        "site at the async grounding gate resolves it via closure."
    )
    assert callable(fn)


# ---------------------------------------------------------------------------
# 2. Behavioural: no-loop path degrades silent (returns None, does not raise).
# ---------------------------------------------------------------------------
def test_grounding_async_no_loop_degrade_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When called from a sync context (no running event loop) the
    scheduler MUST NOT raise — it logs ``grounding_async_no_loop`` at
    debug, closes the unused coroutine, and returns ``None``. The user
    response has already shipped; bubbling RuntimeError up the stack
    would crash the worker for no recoverable reason."""

    def _raises(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(qg.asyncio, "create_task", _raises)

    state = _state()
    # MUST NOT raise.
    result = qg._schedule_grounding_check_background(
        state=state,
        threshold=0.3,
        top_score=0.85,
        model_resolver=_StubResolver(),
        llm=_RecordingLLM(),
    )
    assert result is None
    # State must not have a leaking task entry on the degenerate path.
    assert "grounding_async_task" not in state


# ---------------------------------------------------------------------------
# 3. Behavioural: inside a running loop, the judge is actually invoked.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grounding_async_calls_judge() -> None:
    """When scheduled inside a running event loop the background task
    MUST drive the injected grounding judge — proving the helper wires
    answer + chunks + resolver + LLM through to
    ``OutputGuardrail.llm_grounding_check`` instead of dropping the
    payload on the floor."""
    llm = _RecordingLLM()
    state = _state()
    task = qg._schedule_grounding_check_background(
        state=state,
        threshold=0.3,
        top_score=0.85,
        model_resolver=_StubResolver(),
        llm=llm,
    )
    # Scheduler must have returned a Task (response path was not blocked).
    assert isinstance(task, asyncio.Task)
    assert state.get("grounding_async_task") is task

    # Drain the task so the loop tears down cleanly + the judge ran.
    await task
    assert task.done()
    assert llm.calls, (
        "background scheduler must drive the injected llm.complete; "
        "judge received zero invocations — the helper silently dropped "
        "the answer/chunks payload."
    )
    # The fire-and-forget judge MUST opt into the isolated background semaphore
    # lane so it cannot starve foreground generate under burst (root-cause
    # 2026-06-13). Proven at runtime, not just by source guard.
    assert llm.calls[0].get("background") is True, (
        "async grounding judge must call llm.complete(..., background=True)"
    )
