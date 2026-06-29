"""Deliver-time SSRF guard for ``CallbackDelivery`` (SB-4).

Setup-time validation (``validate_callback_url``) is insufficient on its
own: a DNS-rebinding attacker registers a callback URL whose host resolves
to a *public* IP at validation time, then flips the DNS record so the same
host resolves to an *internal* IP (RFC1918 / loopback / link-local / cloud
metadata ``169.254.169.254``) by the time the worker delivers the answer.

The fix re-resolves the host and rejects private/internal IPs RIGHT BEFORE
the POST. These tests pin:

* URL whose host resolves to a private IP at deliver-time → blocked, no POST.
* Public IP → allowed, POST proceeds.
* Cloud-metadata IP (169.254.169.254) → blocked.
* Guard disabled via flag → POST proceeds even to a private IP (trusted-VPC
  opt-out, secure-by-default ON).
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from ragbot.infrastructure.delivery.callback_delivery import CallbackDelivery


class _MockAsyncClient:
    """Counting stand-in for ``httpx.AsyncClient`` — records POST count."""

    constructions: list["_MockAsyncClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).constructions.append(self)
        self.post_calls = 0
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls += 1
        return httpx.Response(200)

    async def aclose(self) -> None:
        self.closed = True

    @classmethod
    def reset(cls) -> None:
        cls.constructions.clear()


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch):
    _MockAsyncClient.reset()
    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery.httpx.AsyncClient",
        _MockAsyncClient,
    )
    yield
    _MockAsyncClient.reset()


def _patch_resolve(monkeypatch, ip_str: str) -> None:
    """Force ``_is_url_safe``'s DNS resolution to return ``ip_str``."""

    async def _fake_getaddrinfo(host, port, **kwargs):
        return [(2, 1, 6, "", (ip_str, 0))]

    import ragbot.shared.callback_validator as validator

    monkeypatch.setattr(
        validator.asyncio,
        "get_running_loop",
        lambda: type(
            "L", (), {"getaddrinfo": staticmethod(_fake_getaddrinfo)}
        )(),
    )


def test_deliver_blocks_private_ip_at_deliver_time(monkeypatch) -> None:
    """Host resolving to RFC1918 at deliver-time → no POST, returns False."""
    _patch_resolve(monkeypatch, "10.0.0.5")
    delivery = CallbackDelivery(
        callback_url="https://rebind.example.test/webhook",
        max_retries=1,
    )
    ok = asyncio.run(delivery.deliver({"answer": "hi"}))
    assert ok is False, "delivery to a private IP must be blocked"
    assert len(_MockAsyncClient.constructions) == 0 or all(
        c.post_calls == 0 for c in _MockAsyncClient.constructions
    ), "no HTTP POST may be issued when the host resolves internal"


def test_deliver_blocks_cloud_metadata_ip(monkeypatch) -> None:
    """169.254.169.254 (cloud metadata) at deliver-time → blocked."""
    _patch_resolve(monkeypatch, "169.254.169.254")
    delivery = CallbackDelivery(
        callback_url="https://meta.example.test/webhook",
        max_retries=1,
    )
    ok = asyncio.run(delivery.deliver({"answer": "hi"}))
    assert ok is False
    assert all(c.post_calls == 0 for c in _MockAsyncClient.constructions)


def test_deliver_allows_public_ip(monkeypatch) -> None:
    """Host resolving to a public IP at deliver-time → POST proceeds."""
    _patch_resolve(monkeypatch, "8.8.8.8")
    delivery = CallbackDelivery(
        callback_url="https://api.example.test/webhook",
        max_retries=1,
    )
    ok = asyncio.run(delivery.deliver({"answer": "hi"}))
    assert ok is True
    assert sum(c.post_calls for c in _MockAsyncClient.constructions) == 1


def test_deliver_guard_disabled_allows_private_ip(monkeypatch) -> None:
    """When the SSRF guard flag is OFF, a private IP is delivered (opt-out)."""
    _patch_resolve(monkeypatch, "10.0.0.5")
    delivery = CallbackDelivery(
        callback_url="https://internal.example.test/webhook",
        max_retries=1,
        ssrf_guard_enabled=False,
    )
    ok = asyncio.run(delivery.deliver({"answer": "hi"}))
    assert ok is True
    assert sum(c.post_calls for c in _MockAsyncClient.constructions) == 1


def test_guard_default_on() -> None:
    """Secure-by-default: a fresh dispatcher has the SSRF guard enabled."""
    from ragbot.shared.constants import DEFAULT_CALLBACK_SSRF_GUARD_ENABLED

    assert DEFAULT_CALLBACK_SSRF_GUARD_ENABLED is True
    delivery = CallbackDelivery(callback_url="https://example.test/webhook")
    assert delivery._ssrf_guard_enabled is True
