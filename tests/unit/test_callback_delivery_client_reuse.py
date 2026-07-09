"""Finding #12 perf invariant — ``CallbackDelivery`` reuses a single
``httpx.AsyncClient`` across retries AND across dispatcher calls.

Previously the delivery loop instantiated a fresh ``async with
httpx.AsyncClient(...)`` for every retry attempt. Each instance forced
a new TCP handshake (and TLS handshake on HTTPS endpoints) — wasted
~50–150 ms per retry on cross-region webhooks.

The fix lazily creates ONE client per dispatcher instance and reuses it
on every ``deliver()`` attempt + every retry. ``aclose()`` closes the
shared client on shutdown.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from ragbot.infrastructure.delivery.callback_delivery import CallbackDelivery


class _MockAsyncClient:
    """Counting stand-in for ``httpx.AsyncClient``.

    Captures construction count so we can assert exactly ONE instance
    survives across an arbitrary number of retries. ``post`` returns
    a configurable response chain so we can drive both the happy and
    the retry-then-success branches.
    """

    constructions: list["_MockAsyncClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).constructions.append(self)
        self.post_calls = 0
        self._responses: list[httpx.Response] = []
        self.closed = False

    def queue(self, *responses: httpx.Response) -> None:
        """Pre-load responses ``post()`` will pop in order."""
        self._responses.extend(responses)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls += 1
        if not self._responses:
            return httpx.Response(200)
        return self._responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True

    @classmethod
    def reset(cls) -> None:
        cls.constructions.clear()


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch):
    """Replace ``httpx.AsyncClient`` with the counting mock for every test."""
    _MockAsyncClient.reset()
    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery.httpx.AsyncClient",
        _MockAsyncClient,
    )
    # The deliver-time SSRF guard re-resolves the host; these tests exercise
    # client-reuse/retry against RFC-reserved test hosts that do not resolve,
    # so bypass the resolver (SSRF itself is covered by
    # test_chat_worker_callback_negative_paths).
    async def _safe(_url: str):
        return True, ""

    monkeypatch.setattr(
        "ragbot.infrastructure.delivery.callback_delivery._is_url_safe", _safe
    )
    yield
    _MockAsyncClient.reset()


def test_single_client_across_retries() -> None:
    """A single ``deliver()`` triggering 3 retries must construct exactly
    ONE ``AsyncClient`` — not 3.

    The previous code created one per retry; the fix lazily makes one
    on first use and reuses it.
    """
    delivery = CallbackDelivery(
        callback_url="https://example.test/webhook",
        max_retries=3,
        timeout_s=1,
        verify_ssl=False,
    )
    # Two failures + one success — 3 total attempts.
    asyncio.run(_drive(delivery, [
        httpx.Response(500),
        httpx.Response(502),
        httpx.Response(200),
    ]))

    assert len(_MockAsyncClient.constructions) == 1, (
        f"expected exactly 1 AsyncClient; got "
        f"{len(_MockAsyncClient.constructions)} — TCP/TLS handshake reuse "
        "broken"
    )
    client = _MockAsyncClient.constructions[0]
    assert client.post_calls == 3, (
        f"expected 3 retry posts; got {client.post_calls}"
    )


def test_single_client_across_multiple_deliver_calls() -> None:
    """Repeated ``deliver()`` calls on the SAME dispatcher reuse the same
    client. Webhook endpoints under steady load (1 callback per chat
    response × N chats) MUST hit a warm connection pool.
    """
    delivery = CallbackDelivery(
        callback_url="https://example.test/webhook",
        max_retries=1,
        timeout_s=1,
    )

    async def _runs() -> None:
        # 10 sequential deliveries.
        for _ in range(10):
            await delivery.deliver({"answer": "hi"})

    asyncio.run(_runs())

    assert len(_MockAsyncClient.constructions) == 1, (
        f"expected 1 reused AsyncClient across 10 deliveries; got "
        f"{len(_MockAsyncClient.constructions)}"
    )
    assert _MockAsyncClient.constructions[0].post_calls == 10


def test_aclose_releases_client() -> None:
    """``aclose()`` calls the underlying client's ``aclose`` so the pool
    drains cleanly on shutdown. Subsequent ``deliver()`` MAY rebuild —
    we don't pin that behaviour because shutdown ordering is caller-controlled.
    """
    delivery = CallbackDelivery(
        callback_url="https://example.test/webhook",
        max_retries=1,
    )

    async def _runs() -> None:
        await delivery.deliver({"answer": "hi"})
        await delivery.aclose()

    asyncio.run(_runs())

    assert len(_MockAsyncClient.constructions) == 1
    assert _MockAsyncClient.constructions[0].closed is True


def test_aclose_idempotent() -> None:
    """``aclose()`` without prior use must NOT raise — defensive shutdown
    ordering: the caller may close before the first delivery."""
    delivery = CallbackDelivery(
        callback_url="https://example.test/webhook",
        max_retries=1,
    )
    # Must not raise.
    asyncio.run(delivery.aclose())
    assert len(_MockAsyncClient.constructions) == 0


def test_concurrent_deliver_only_constructs_once() -> None:
    """Two concurrent ``deliver()`` coroutines on a cold dispatcher must
    race exactly once through the lazy-init lock — the second call
    sees the already-built client and skips construction.
    """
    delivery = CallbackDelivery(
        callback_url="https://example.test/webhook",
        max_retries=1,
    )

    async def _runs() -> None:
        await asyncio.gather(
            delivery.deliver({"i": 1}),
            delivery.deliver({"i": 2}),
            delivery.deliver({"i": 3}),
        )

    asyncio.run(_runs())

    assert len(_MockAsyncClient.constructions) == 1, (
        f"expected exactly 1 AsyncClient under concurrent first-use; got "
        f"{len(_MockAsyncClient.constructions)} — lazy-init lock leaking"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _drive(delivery: CallbackDelivery, responses: list[httpx.Response]) -> None:
    """Queue ``responses`` onto the next ``_MockAsyncClient`` instance,
    then fire one ``deliver()``. The mock's ``post`` consumes them
    in order.
    """
    # Pre-create the client through the dispatcher's lazy path so we can
    # queue the responses on it BEFORE deliver issues the post.
    client = await delivery._get_client()  # type: ignore[attr-defined]
    assert isinstance(client, _MockAsyncClient)
    client.queue(*responses)
    # Patch backoff to 0 so the test runs fast.
    import ragbot.infrastructure.delivery.callback_delivery as mod

    original_sleep = mod.asyncio.sleep

    async def _no_sleep(*_a, **_kw):  # type: ignore[no-untyped-def]
        return None

    mod.asyncio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        await delivery.deliver({"answer": "hi"})
    finally:
        mod.asyncio.sleep = original_sleep  # type: ignore[assignment]
