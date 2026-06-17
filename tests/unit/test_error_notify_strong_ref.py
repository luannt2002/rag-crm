"""Per Python docs, the asyncio event loop only keeps weak references to
tasks created by ``create_task``. A fire-and-forget pattern that drops
the returned handle is therefore at risk of getting GC'd before the
dispatch coroutine finishes its webhook round-trip.

``ErrorNotifyHook`` should hold a strong reference for every scheduled
task and release it via a done-callback so the registry stays bounded.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ragbot.application.services.error_notify_hook import ErrorNotifyHook
from ragbot.shared.errors import InfrastructureError


class _RecordingDispatcher:
    def __init__(self, slow_ms: int = 50) -> None:
        self.calls: list[dict] = []
        self.slow_ms = slow_ms

    async def dispatch(self, **kw: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(self.slow_ms / 1000)
        self.calls.append(kw)


@pytest.mark.asyncio
async def test_error_notify_hook_keeps_strong_ref_on_pending_tasks() -> None:
    disp = _RecordingDispatcher(slow_ms=20)
    hook = ErrorNotifyHook(disp)

    task = await hook.on_ai_error(
        error=InfrastructureError("boom"), component="test.unit",
    )
    assert task is not None

    # Confirm hook tracks pending tasks via a strong-ref registry.
    pending = getattr(hook, "_pending_tasks", None)
    assert pending is not None, (
        "ErrorNotifyHook must expose a _pending_tasks set so the asyncio "
        "event loop's weak-reference behaviour cannot drop the dispatch "
        "task before the webhook completes"
    )
    assert task in pending
    await task
    # Done-callback removes it.
    assert task not in pending


@pytest.mark.asyncio
async def test_strong_ref_set_drains_after_concurrent_dispatch() -> None:
    disp = _RecordingDispatcher(slow_ms=10)
    hook = ErrorNotifyHook(disp)

    tasks = []
    for i in range(5):
        t = await hook.on_ai_error(
            error=InfrastructureError(f"e{i}"), component="test.unit",
        )
        tasks.append(t)
    await asyncio.gather(*[t for t in tasks if t is not None])

    pending = getattr(hook, "_pending_tasks")
    assert len(pending) == 0
    assert len(disp.calls) == 5
