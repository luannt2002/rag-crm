"""Pin tests — callback negative paths: timeout, 5xx, malformed URL.

Three test groups:
1. Timeout — ``asyncio.TimeoutError`` from the HTTP client is retried, not fatal.
2. 5xx all retries — ``deliver()`` returns ``False`` (not raises).
3. Malformed callback_url — ``_is_url_safe`` rejects non-parseable inputs.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ragbot.infrastructure.delivery.callback_delivery import CallbackDelivery
from ragbot.shared.callback_validator import _is_url_safe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TimeoutClient:
    """Always raises ``asyncio.TimeoutError`` (simulates endpoint timeout)."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.call_count = 0
        self.closed = False

    async def post(self, *_: Any, **__: Any) -> None:
        self.call_count += 1
        raise asyncio.TimeoutError("read timeout")

    async def aclose(self) -> None:
        self.closed = True


class _Always5xxClient:
    """Always returns 500."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.call_count = 0
        self.closed = False

    async def post(self, *_: Any, **__: Any) -> httpx.Response:
        self.call_count += 1
        return httpx.Response(500)

    async def aclose(self) -> None:
        self.closed = True


def _make(client: Any, **kwargs: Any) -> CallbackDelivery:
    d = CallbackDelivery(callback_url="https://ep.example.com/wh", **kwargs)
    d._client = client  # type: ignore[attr-defined]
    return d


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Group 1: Timeout
# ---------------------------------------------------------------------------


def test_timeout_error_is_caught_and_retried():
    """``asyncio.TimeoutError`` does NOT propagate — it is caught and counted
    as a failed attempt. After ``max_retries`` the call returns ``False``.
    """
    client = _TimeoutClient()
    delivery = _make(client, max_retries=2, backoff_base_s=0.0)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is False
    assert client.call_count == 2  # retried, didn't explode


def test_timeout_then_success():
    """First attempt times out; second attempt succeeds → ``True``."""

    class _FirstTimeoutThenOk:
        def __init__(self, *_: Any, **__: Any) -> None:
            self.call_count = 0
            self.closed = False

        async def post(self, *_: Any, **__: Any):
            self.call_count += 1
            if self.call_count == 1:
                raise asyncio.TimeoutError("first attempt timeout")
            return httpx.Response(200)

        async def aclose(self) -> None:
            self.closed = True

    client = _FirstTimeoutThenOk()
    delivery = _make(client, max_retries=2, backoff_base_s=0.0)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is True
    assert client.call_count == 2


# ---------------------------------------------------------------------------
# Group 2: 5xx all retries exhausted
# ---------------------------------------------------------------------------


def test_5xx_all_retries_returns_false_not_raises():
    """When every attempt returns 5xx, ``deliver()`` returns ``False``.

    It must NOT raise an exception — delivery failures are caller-visible
    via the boolean return value, not exceptions.
    """
    client = _Always5xxClient()
    delivery = _make(client, max_retries=3, backoff_base_s=0.0)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        # Must not raise.
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is False
    assert client.call_count == 3


def test_404_is_counted_as_failure():
    """4xx is also treated as failure (``status_code >= 400``)."""

    class _404Client:
        def __init__(self, *_: Any, **__: Any) -> None:
            self.call_count = 0
            self.closed = False

        async def post(self, *_: Any, **__: Any) -> httpx.Response:
            self.call_count += 1
            return httpx.Response(404)

        async def aclose(self) -> None:
            self.closed = True

    client = _404Client()
    delivery = _make(client, max_retries=2, backoff_base_s=0.0)

    with patch("ragbot.infrastructure.delivery.callback_delivery.asyncio.sleep", new=AsyncMock()):
        ok = _run(delivery.deliver({"ok": True}))

    assert ok is False


# ---------------------------------------------------------------------------
# Group 3: Malformed / invalid callback_url
# ---------------------------------------------------------------------------


def test_malformed_url_empty_string():
    """Empty string callback_url → scheme invalid → rejected."""
    ok, reason = _run(_is_url_safe(""))
    assert ok is False


def test_malformed_url_no_scheme():
    """URL without scheme (e.g. ``example.com/path``) → rejected."""
    ok, reason = _run(_is_url_safe("example.com/path"))
    assert ok is False


def test_malformed_url_javascript_scheme():
    """``javascript://`` scheme is rejected immediately."""
    ok, reason = _run(_is_url_safe("javascript://alert(1)"))
    assert ok is False


def test_malformed_url_file_scheme():
    """``file://`` scheme is rejected immediately."""
    ok, reason = _run(_is_url_safe("file:///etc/passwd"))
    assert ok is False
