"""Unit tests for ``WebhookNotifyDispatcher``.

The HTTP layer is exercised via ``httpx.MockTransport`` so the suite is
deterministic + offline. Redis + resolver are stubbed so dedup +
rate-limit + unconfigured paths are testable in isolation.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ragbot.application.dto.notify_channel import NotifyChannelConfig
from ragbot.infrastructure.notify.webhook_dispatcher import WebhookNotifyDispatcher


_CONFIG_DICT = {
    "method": "POST",
    "domain": "https://example.com",
    "path_template": "/hooks/{conversation_id}/in",
    "conversation_id": "conv-1",
    "webhook_key": "whk_test_key",
    "enabled": True,
    "timeout_s": 1.0,
    "max_retries": 2,
}


def _config(**overrides: Any) -> NotifyChannelConfig:
    return NotifyChannelConfig.model_validate({**_CONFIG_DICT, **overrides})


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._counter: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def incr(self, key: str) -> int:
        self._counter[key] = self._counter.get(key, 0) + 1
        return self._counter[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


class _StubResolver:
    def __init__(self, cfg: NotifyChannelConfig | None, source: str = "env") -> None:
        self._cfg = cfg
        self._source = source

    async def resolve(self):
        return self._cfg, self._source

    async def invalidate(self) -> None:
        return None


def _build_dispatcher(
    *,
    handler,
    resolver,
    redis,
    rate_limit_per_min: int = 30,
    dedup_window_s: int = 60,
) -> WebhookNotifyDispatcher:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return WebhookNotifyDispatcher(
        httpx_client=client,
        resolver=resolver,
        redis_client=redis,
        rate_limit_per_min=rate_limit_per_min,
        dedup_window_s=dedup_window_s,
    )


@pytest.mark.asyncio
async def test_dispatch_happy_path_returns_2xx():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"ok": True})

    cfg = _config()
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=_FakeRedis(),
    )
    out = await dispatcher.dispatch(
        severity="error", component="chat.pipeline", message="boom",
        error_type="LLMError",
    )

    assert out["dispatched"] is True
    assert out["upstream_status"] == 200
    assert seen["url"] == "https://example.com/hooks/conv-1/in"
    assert seen["headers"]["x-webhook-key"] == "whk_test_key"
    body_text = seen["body"]
    assert "[RAGBOT-ALERT] error" in body_text
    assert "LLMError" in body_text
    assert "chat.pipeline" in body_text


@pytest.mark.asyncio
async def test_dispatch_4xx_drops_no_retry():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(404, json={"error": "wrong path"})

    cfg = _config()
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=_FakeRedis(),
    )
    out = await dispatcher.dispatch(
        severity="error", component="chat.pipeline", message="boom",
    )

    assert out["dispatched"] is False
    assert out["upstream_status"] == 404
    # 4xx must NOT trigger retry — config bug, retry would not help.
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_dispatch_5xx_retries_then_gives_up():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(503, text="upstream unavailable")

    cfg = _config(max_retries=2)
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=_FakeRedis(),
    )
    out = await dispatcher.dispatch(
        severity="error", component="chat.pipeline", message="boom",
    )

    assert out["dispatched"] is False
    assert out["upstream_status"] == 503
    # max_retries=2 → 1 initial + 2 retries = 3 attempts.
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_dispatch_dedups_identical_messages():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200)

    cfg = _config()
    redis = _FakeRedis()
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=redis,
    )

    out1 = await dispatcher.dispatch(
        severity="error", component="chat.pipeline", message="same message",
    )
    out2 = await dispatcher.dispatch(
        severity="error", component="chat.pipeline", message="same message",
    )

    assert out1["dispatched"] is True
    assert out2["dispatched"] is False
    assert out2["reason"] == "dedup"
    # Only the first message reached the upstream.
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_dispatch_rate_limit_drops_excess():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200)

    cfg = _config()
    redis = _FakeRedis()
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=redis,
        rate_limit_per_min=2,
    )

    # 3 distinct messages so dedup does not interfere; dispatch #3 must
    # hit the rate-limit drop.
    out1 = await dispatcher.dispatch(
        severity="error", component="c", message="msg-1",
    )
    out2 = await dispatcher.dispatch(
        severity="error", component="c", message="msg-2",
    )
    out3 = await dispatcher.dispatch(
        severity="error", component="c", message="msg-3",
    )

    assert out1["dispatched"] is True
    assert out2["dispatched"] is True
    assert out3["dispatched"] is False
    assert out3["reason"] == "rate_limit"


@pytest.mark.asyncio
async def test_dispatch_disabled_config_drops():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called when enabled=False")

    cfg = _config(enabled=False)
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=_FakeRedis(),
    )
    out = await dispatcher.dispatch(
        severity="error", component="c", message="m",
    )

    assert out["dispatched"] is False
    assert out["reason"] == "disabled"


@pytest.mark.asyncio
async def test_dispatch_unconfigured_drops():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called when config is None")

    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(None, source="none"),
        redis=_FakeRedis(),
    )
    out = await dispatcher.dispatch(
        severity="error", component="c", message="m",
    )

    assert out["dispatched"] is False
    assert out["reason"] == "unconfigured"


@pytest.mark.asyncio
async def test_dispatch_swallows_self_failure():
    """Network exception must NOT propagate out of ``dispatch``."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    cfg = _config(max_retries=0)
    dispatcher = _build_dispatcher(
        handler=handler, resolver=_StubResolver(cfg), redis=_FakeRedis(),
    )

    # Must not raise; outcome reflects the failed dispatch.
    out = await dispatcher.dispatch(
        severity="error", component="c", message="m",
    )

    assert out["dispatched"] is False
    assert out["upstream_status"] is None
