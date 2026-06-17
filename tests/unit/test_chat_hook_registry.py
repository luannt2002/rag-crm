"""Unit tests for ChatHookRegistry — 2-stage dispatch + isolation guarantees."""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from ragbot.application.events.chat_completed import (
    ChatCompletedEvent,
    ChatHookRegistry,
)


def _make_event() -> ChatCompletedEvent:
    return ChatCompletedEvent(
        record_tenant_id=uuid4(),
        workspace_id="ws-test",
        bot_id="bot-test",
        channel_type="web",
        record_bot_id=uuid4(),
        request_id=uuid4(),
        prompt_tokens=10,
        completion_tokens=20,
        tokens_used_delta=30,
        refusal_reason=None,
        intent="qa",
        timestamp_iso="2026-05-14T00:00:00Z",
    )


class _RecordingHook:
    """Test double: records each invocation."""

    def __init__(self, name: str, stage: str, *, raises: Exception | None = None,
                 sleep_s: float = 0.0, log: list[str] | None = None):
        self._name = name
        self._stage = stage
        self._raises = raises
        self._sleep_s = sleep_s
        self._log = log if log is not None else []

    @property
    def hook_name(self) -> str:
        return self._name

    @property
    def stage(self) -> str:
        return self._stage

    async def run(self, event: ChatCompletedEvent, *, session: Any) -> None:
        if self._sleep_s > 0:
            await asyncio.sleep(self._sleep_s)
        self._log.append(self._name)
        if self._raises is not None:
            raise self._raises


@pytest.mark.asyncio
async def test_db_stage_runs_db_hooks_only():
    log: list[str] = []
    hooks = [
        _RecordingHook("db1", "db", log=log),
        _RecordingHook("post1", "post_commit", log=log),
        _RecordingHook("db2", "db", log=log),
    ]
    reg = ChatHookRegistry(hooks)
    result = await reg.fire_db_stage(_make_event(), session=None)
    assert result == {"db1": True, "db2": True}
    assert log == ["db1", "db2"]


@pytest.mark.asyncio
async def test_post_stage_runs_post_hooks_only():
    log: list[str] = []
    hooks = [
        _RecordingHook("db1", "db", log=log),
        _RecordingHook("post1", "post_commit", log=log),
        _RecordingHook("post2", "post_commit", log=log),
    ]
    reg = ChatHookRegistry(hooks)
    result = await reg.fire_post_stage(_make_event(), session=None)
    assert result == {"post1": True, "post2": True}
    assert log == ["post1", "post2"]


@pytest.mark.asyncio
async def test_hook_exception_isolated_does_not_kill_others():
    log: list[str] = []
    hooks = [
        _RecordingHook("ok1", "db", log=log),
        _RecordingHook("boom", "db", raises=RuntimeError("kaboom"), log=log),
        _RecordingHook("ok2", "db", log=log),
    ]
    reg = ChatHookRegistry(hooks)
    result = await reg.fire_db_stage(_make_event(), session=None)
    assert result == {"ok1": True, "boom": False, "ok2": True}
    # All three reached the recording line (boom raises after append)
    assert log == ["ok1", "boom", "ok2"]


@pytest.mark.asyncio
async def test_hook_timeout_isolated():
    log: list[str] = []
    hooks = [
        _RecordingHook("fast1", "db", log=log),
        _RecordingHook("slow", "db", sleep_s=5.0, log=log),
        _RecordingHook("fast2", "db", log=log),
    ]
    reg = ChatHookRegistry(hooks, timeout_s=0.05)
    result = await reg.fire_db_stage(_make_event(), session=None)
    assert result == {"fast1": True, "slow": False, "fast2": True}
    # slow never appended (timed out before sleep finished)
    assert "slow" not in log
    assert "fast1" in log and "fast2" in log


@pytest.mark.asyncio
async def test_hooks_run_in_registration_order():
    log: list[str] = []
    hooks = [
        _RecordingHook(f"h{i}", "db", log=log) for i in range(5)
    ]
    reg = ChatHookRegistry(hooks, max_concurrency=1)
    await reg.fire_db_stage(_make_event(), session=None)
    assert log == ["h0", "h1", "h2", "h3", "h4"]


@pytest.mark.asyncio
async def test_empty_registry_returns_empty_results():
    reg = ChatHookRegistry([])
    db_result = await reg.fire_db_stage(_make_event(), session=None)
    post_result = await reg.fire_post_stage(_make_event(), session=None)
    assert db_result == {}
    assert post_result == {}


@pytest.mark.asyncio
async def test_semaphore_bounds_concurrency():
    """With max_concurrency=5 and 10 slow hooks, never more than 5 in flight."""
    concurrent_now = 0
    peak = 0
    lock = asyncio.Lock()

    class _ProbeHook:
        def __init__(self, name: str):
            self._name = name

        @property
        def hook_name(self) -> str:
            return self._name

        @property
        def stage(self) -> str:
            return "db"

        async def run(self, event: ChatCompletedEvent, *, session: Any) -> None:
            nonlocal concurrent_now, peak
            async with lock:
                concurrent_now += 1
                if concurrent_now > peak:
                    peak = concurrent_now
            await asyncio.sleep(0.05)
            async with lock:
                concurrent_now -= 1

    hooks = [_ProbeHook(f"p{i}") for i in range(10)]
    reg = ChatHookRegistry(hooks, max_concurrency=5, timeout_s=5.0)
    # Drive all hooks concurrently via asyncio.gather on individual fires
    # fire_db_stage uses sequential dict comprehension, so spawn fires in parallel
    await asyncio.gather(*[
        reg._run_isolated(h, _make_event(), None) for h in hooks
    ])
    assert peak <= 5, f"Semaphore breached: peak concurrency = {peak}"
    assert peak >= 2, f"Test didn't actually overlap (peak={peak})"
