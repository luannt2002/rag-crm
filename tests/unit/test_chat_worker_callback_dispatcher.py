"""Pin test — ``CallbackDelivery.deliver()`` POSTs result to callback_url.

Verifies:
- Successful delivery returns ``True``
- ``httpx.AsyncClient.post`` called with the correct URL + JSON body
- HMAC signature header present when ``hmac_secret`` supplied
- ``mode_name`` property returns "callback"
- ``NoopDelivery`` returned when ``callback_url`` is None
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ragbot.infrastructure.delivery import create_delivery
from ragbot.infrastructure.delivery.callback_delivery import CallbackDelivery
from ragbot.infrastructure.delivery.noop_delivery import NoopDelivery


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeClient:
    """Stand-in for ``httpx.AsyncClient`` that captures calls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[dict] = []
        self.closed = False
        self._response = _FakeResponse(200)

    async def post(self, url: str, *, content: bytes, headers: dict) -> _FakeResponse:
        self.calls.append({"url": url, "body": json.loads(content), "headers": headers})
        return self._response

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


def _run_delivery(delivery: CallbackDelivery, payload: dict) -> bool:
    """Helper: run ``deliver`` synchronously via ``asyncio.run``."""
    return asyncio.run(delivery.deliver(payload))


def test_deliver_posts_to_callback_url(monkeypatch):
    """``deliver()`` POSTs to the configured URL with the result payload."""
    fake = _FakeClient()
    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery.httpx.AsyncClient",
        lambda **_: fake,
    )

    delivery = CallbackDelivery(
        callback_url="https://partner.example.io/cb",
        max_retries=1,
        timeout_s=5,
    )
    result = {"ok": True, "job_id": "abc-123", "answer": "hello"}

    ok = _run_delivery(delivery, result)

    assert ok is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == "https://partner.example.io/cb"
    assert fake.calls[0]["body"]["job_id"] == "abc-123"
    assert fake.calls[0]["body"]["answer"] == "hello"


def test_deliver_includes_hmac_header(monkeypatch):
    """When ``hmac_secret`` is set, ``X-Ragbot-Signature`` header is present."""
    fake = _FakeClient()
    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery.httpx.AsyncClient",
        lambda **_: fake,
    )

    delivery = CallbackDelivery(
        callback_url="https://partner.example.io/cb",
        hmac_secret="supersecret",
        max_retries=1,
        timeout_s=5,
    )
    _run_delivery(delivery, {"ok": True})

    assert "X-Ragbot-Signature" in fake.calls[0]["headers"]
    assert fake.calls[0]["headers"]["X-Ragbot-Signature"].startswith("sha256=")
    assert "X-Ragbot-Timestamp" in fake.calls[0]["headers"]


def test_deliver_no_hmac_when_no_secret(monkeypatch):
    """No HMAC header when ``hmac_secret`` is empty."""
    fake = _FakeClient()
    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery.httpx.AsyncClient",
        lambda **_: fake,
    )

    delivery = CallbackDelivery(
        callback_url="https://partner.example.io/cb",
        hmac_secret="",
        max_retries=1,
    )
    _run_delivery(delivery, {"ok": True})

    assert "X-Ragbot-Signature" not in fake.calls[0]["headers"]


def test_mode_name_is_callback():
    """``CallbackDelivery.mode_name`` always returns "callback"."""
    d = CallbackDelivery(callback_url="https://example.com/x", max_retries=1)
    assert d.mode_name == "callback"


def test_noop_delivery_when_callback_url_is_none():
    """``create_delivery(callback_url=None)`` returns ``NoopDelivery``.

    ``NoopDelivery.mode_name`` is "poll" — caller polls the job status
    endpoint rather than receiving a push callback.
    """
    delivery = create_delivery(callback_url=None)
    assert isinstance(delivery, NoopDelivery)
    assert delivery.mode_name == "poll"


def test_callback_delivery_returned_when_url_provided():
    """``create_delivery(callback_url="https://…")`` returns ``CallbackDelivery``."""
    delivery = create_delivery(callback_url="https://example.com/webhook")
    assert isinstance(delivery, CallbackDelivery)
