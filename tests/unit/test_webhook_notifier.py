"""Unit tests for ``WebhookNotifier``.

The HTTP layer is exercised via ``httpx.MockTransport`` so the suite is
deterministic and offline. Redis is stubbed via a fake with the same
SETNX semantics the adapter relies on.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from ragbot.application.ports.notify_channel_port import NotifyChannelPort
from ragbot.infrastructure.notify.webhook_notifier import (
    WebhookNotifier,
    NullNotifier,
)


class _FakeRedis:
    """Minimal stub mirroring ``redis.asyncio.Redis.set(nx=True)``."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple[str, dict[str, Any]]] = []

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        self.set_calls.append((key, {"value": value, "ex": ex, "nx": nx}))
        if nx and key in self._store:
            return None  # SETNX miss = throttled
        self._store[key] = value
        return True


class _FakeRedisAlwaysFails:
    """Stub that raises on every operation — exercises Redis-down path."""

    async def set(self, *args: Any, **kwargs: Any) -> bool | None:
        raise ConnectionError("redis down")


def _make_notifier(
    *,
    url: str = "https://operator.example/webhook",
    auth: str = "tok-123",
    redis: Any | None = None,
) -> WebhookNotifier:
    return WebhookNotifier(
        url=url,
        auth_token=auth,
        redis_client=redis if redis is not None else _FakeRedis(),
    )


@pytest.mark.asyncio
async def test_empty_url_silently_disabled() -> None:
    """No URL configured → adapter returns False without touching Redis."""
    redis = _FakeRedis()
    notifier = _make_notifier(url="", redis=redis)

    sent = await notifier.send_quota_exhausted(
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        bot_name="alpha",
        tokens_used=1_000_000,
        effective_limit=500_000,
    )

    assert sent is False
    assert redis.set_calls == []  # never even checked throttle


@pytest.mark.asyncio
async def test_successful_post_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: 202 from upstream → return True, payload well-formed."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(202, json={"ok": True})

    transport = httpx.MockTransport(_handler)

    # Patch httpx.AsyncClient so the adapter's ``async with`` uses our
    # mock transport without modifying production code.
    real_async_client = httpx.AsyncClient

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "ragbot.infrastructure.notify.webhook_notifier.httpx.AsyncClient",
        _patched_client,
    )

    notifier = _make_notifier()
    tenant_id = uuid4()
    bot_id = uuid4()

    sent = await notifier.send_quota_exhausted(
        record_tenant_id=tenant_id,
        record_bot_id=bot_id,
        bot_name="alpha",
        tokens_used=12_345,
        effective_limit=10_000,
    )

    assert sent is True
    assert captured["url"] == "https://operator.example/webhook"
    assert captured["headers"]["authorization"] == "Bearer tok-123"
    assert captured["headers"]["content-type"] == "application/json"
    # Body sanity — parse JSON so we don't depend on whitespace style.
    import json as _json

    payload = _json.loads(captured["body"])
    assert payload["event"] == "bot_quota_exhausted"
    assert payload["record_tenant_id"] == str(tenant_id)
    assert payload["record_bot_id"] == str(bot_id)
    assert payload["bot_name"] == "alpha"
    assert payload["tokens_used"] == 12_345
    assert payload["effective_limit"] == 10_000
    assert payload["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_http_error_returns_false_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport failure (httpx error) → return False, never raise."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down")

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "ragbot.infrastructure.notify.webhook_notifier.httpx.AsyncClient",
        _patched_client,
    )

    notifier = _make_notifier()
    sent = await notifier.send_quota_exhausted(
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        bot_name="alpha",
        tokens_used=1,
        effective_limit=1,
    )

    assert sent is False


@pytest.mark.asyncio
async def test_throttled_no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call within throttle window → no POST, return False."""
    redis = _FakeRedis()
    bot_id = uuid4()
    # Pre-seed the throttle key so the FIRST adapter call sees a miss.
    redis._store[f"ragbot:notify:quota:{bot_id}"] = "1"

    post_called = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        post_called["n"] += 1
        return httpx.Response(200)

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "ragbot.infrastructure.notify.webhook_notifier.httpx.AsyncClient",
        _patched_client,
    )

    notifier = _make_notifier(redis=redis)
    sent = await notifier.send_quota_exhausted(
        record_tenant_id=uuid4(),
        record_bot_id=bot_id,
        bot_name="alpha",
        tokens_used=1,
        effective_limit=1,
    )

    assert sent is False
    assert post_called["n"] == 0  # never POSTed
    # Throttle SET still attempted (with nx=True), then short-circuited.
    assert redis.set_calls[0][1]["nx"] is True


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis throttle check raises → adapter still sends (safer than miss)."""
    post_called = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        post_called["n"] += 1
        return httpx.Response(200)

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "ragbot.infrastructure.notify.webhook_notifier.httpx.AsyncClient",
        _patched_client,
    )

    notifier = _make_notifier(redis=_FakeRedisAlwaysFails())
    sent = await notifier.send_quota_exhausted(
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        bot_name="alpha",
        tokens_used=1,
        effective_limit=1,
    )

    assert sent is True
    assert post_called["n"] == 1


@pytest.mark.asyncio
async def test_null_notifier_returns_false() -> None:
    """``NullNotifier`` is the always-False default — must satisfy port."""
    notifier: NotifyChannelPort = NullNotifier()
    sent = await notifier.send_quota_exhausted(
        record_tenant_id=uuid4(),
        record_bot_id=uuid4(),
        bot_name="alpha",
        tokens_used=1,
        effective_limit=1,
    )
    assert sent is False
    assert isinstance(notifier, NotifyChannelPort)
