"""Unit tests for ``ErrorNotifyHook``."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ragbot.application.services.error_notify_hook import ErrorNotifyHook
from ragbot.shared.errors import (
    CircuitBreakerOpen,
    EmbeddingError,
    LLMError,
)


class _StubDispatcher:
    """Records every call so tests can assert exact wire shape."""

    def __init__(self, *, raise_on_dispatch: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise = raise_on_dispatch

    async def dispatch(self, **kwargs: Any) -> dict[str, Any]:
        if self._raise is not None:
            raise self._raise
        self.calls.append(kwargs)
        return {"dispatched": True, "reason": None, "upstream_status": 200}


@pytest.mark.asyncio
async def test_on_ai_error_schedules_dispatch_for_llm_error():
    dispatcher = _StubDispatcher()
    hook = ErrorNotifyHook(dispatcher=dispatcher)

    task = await hook.on_ai_error(
        error=LLMError("provider exhausted retries"),
        component="chat.pipeline",
    )

    assert isinstance(task, asyncio.Task)
    await task  # let the scheduled coroutine run

    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert call["severity"] == "error"
    assert call["component"] == "chat.pipeline"
    assert call["error_type"] == "LLMError"
    assert "provider exhausted" in call["message"]


@pytest.mark.asyncio
async def test_on_ai_error_marks_circuit_breaker_critical():
    dispatcher = _StubDispatcher()
    hook = ErrorNotifyHook(dispatcher=dispatcher)

    task = await hook.on_ai_error(
        error=CircuitBreakerOpen("rerank breaker"),
        component="chat.reranker",
    )
    await task

    assert dispatcher.calls[0]["severity"] == "critical"
    assert dispatcher.calls[0]["error_type"] == "CircuitBreakerOpen"


@pytest.mark.asyncio
async def test_on_ai_error_swallows_dispatch_scheduling_failure():
    """Hook must NEVER propagate scheduling failures up to the caller."""

    class _Boom:
        async def dispatch(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("scheduling broken")

    # We force a scheduling-time failure by passing a dispatcher that
    # raises *during* coroutine construction. ``hook.on_ai_error`` builds
    # the coroutine inside the try-block so the exception path is hit.
    class _NoDispatch:
        def dispatch(self, **kwargs: Any):  # not async — calling it raises
            raise RuntimeError("not awaitable")

    hook = ErrorNotifyHook(dispatcher=_NoDispatch())

    # Must return None and not raise.
    result = await hook.on_ai_error(
        error=EmbeddingError("embedder down"),
        component="ingest.pipeline",
    )
    assert result is None


@pytest.mark.asyncio
async def test_on_ai_error_returns_task_handle_for_observation():
    dispatcher = _StubDispatcher()
    hook = ErrorNotifyHook(dispatcher=dispatcher)

    task = await hook.on_ai_error(
        error=EmbeddingError("no embedder"),
        component="ingest.pipeline",
    )

    assert isinstance(task, asyncio.Task)
    # Tests can await the handle to ensure dispatch ran.
    await task
    assert dispatcher.calls[0]["component"] == "ingest.pipeline"
    assert dispatcher.calls[0]["severity"] == "error"
