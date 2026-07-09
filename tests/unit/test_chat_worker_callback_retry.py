"""Pin test — ``CallbackDelivery`` retry behaviour (fail-then-succeed).

Verifies:
- Transient 5xx + network errors are retried up to ``max_retries``
- First failure + subsequent success returns ``True`` (not ``False``)
- All retries exhausted returns ``False``
- Exponential backoff sleep is called between attempts
- ``DEFAULT_CALLBACK_MAX_RETRIES`` and ``DEFAULT_CALLBACK_BACKOFF_BASE_S``
  are used as constructor defaults (zero-hardcode guard)
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ragbot.infrastructure.delivery.callback_delivery import CallbackDelivery
from ragbot.shared.constants import (
    DEFAULT_CALLBACK_BACKOFF_BASE_S,
    DEFAULT_CALLBACK_MAX_RETRIES,
)


@pytest.fixture(autouse=True)
def _bypass_ssrf_guard(monkeypatch):
    """Deliver-time SSRF guard re-resolves the host; these tests use
    RFC-reserved hosts that do not resolve, so bypass the resolver (SSRF is
    covered by test_chat_worker_callback_negative_paths)."""
    async def _safe(_url: str):
        return True, ""

    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery._is_url_safe", _safe
    )


class _SequentialClient:
    """Returns status codes in sequence; raises ``httpx.ConnectError`` when list exhausted."""

    def __init__(self, statuses: list[int | type], *, raises: list[Exception | None] | None = None) -> None:
        self._statuses = list(statuses)
        self._raises: list[Exception | None] = raises or [None] * len(statuses)
        self.call_count = 0
        self.closed = False

    async def post(self, url: str, **kwargs: Any):
        if self.call_count >= len(self._statuses):
            raise httpx.ConnectError("exhausted")
        exc = self._raises[self.call_count]
        status = self._statuses[self.call_count]
        self.call_count += 1
        if exc is not None:
            raise exc
        return httpx.Response(status)

    async def aclose(self) -> None:
        self.closed = True


def _make_delivery(client: _SequentialClient, **kwargs) -> CallbackDelivery:
    delivery = CallbackDelivery(
        callback_url="https://partner.example.com/webhook",
        **kwargs,
    )
    delivery._client = client  # type: ignore[attr-defined]
    return delivery


def _run(coro) -> Any:
    return asyncio.run(coro)


def test_fail_then_succeed_returns_true(monkeypatch):
    """Two 5xx failures then one 200: ``deliver`` returns ``True``."""
    client = _SequentialClient([500, 503, 200])
    delivery = _make_delivery(client, max_retries=3, backoff_base_s=0.0)

    # Patch sleep so test runs instantly.
    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is True
    assert client.call_count == 3


def test_all_retries_exhausted_returns_false(monkeypatch):
    """When all ``max_retries`` fail the delivery returns ``False``."""
    client = _SequentialClient([500, 500, 500])
    delivery = _make_delivery(client, max_retries=3, backoff_base_s=0.0)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is False
    assert client.call_count == 3


def test_network_error_is_retried(monkeypatch):
    """``httpx.ConnectError`` is caught + retried."""
    exc = httpx.ConnectError("refused")
    client = _SequentialClient([0, 200], raises=[exc, None])
    delivery = _make_delivery(client, max_retries=2, backoff_base_s=0.0)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is True
    assert client.call_count == 2


def test_backoff_sleep_called_between_retries(monkeypatch):
    """Exponential backoff ``asyncio.sleep`` is called between retry attempts."""
    client = _SequentialClient([500, 500, 200])
    delivery = _make_delivery(client, max_retries=3, backoff_base_s=1.0)

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", side_effect=_fake_sleep):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is True
    # Two failures → two sleeps before the last successful attempt.
    assert len(sleep_calls) == 2
    # Backoff: 1.0 * 2^0 = 1.0, 1.0 * 2^1 = 2.0
    assert sleep_calls == [1.0, 2.0]


def test_default_max_retries_from_constants():
    """Constructor default ``max_retries`` uses ``DEFAULT_CALLBACK_MAX_RETRIES``."""
    d = CallbackDelivery(callback_url="https://example.com/wh")
    assert d._max_retries == DEFAULT_CALLBACK_MAX_RETRIES  # type: ignore[attr-defined]


def test_default_backoff_base_from_constants():
    """Constructor default ``backoff_base_s`` uses ``DEFAULT_CALLBACK_BACKOFF_BASE_S``."""
    d = CallbackDelivery(callback_url="https://example.com/wh")
    assert d._backoff_base_s == DEFAULT_CALLBACK_BACKOFF_BASE_S  # type: ignore[attr-defined]
