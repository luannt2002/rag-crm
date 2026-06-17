"""Tests for RedisStreamsEventBus.recover_pending_messages."""

from __future__ import annotations

import inspect

import pytest

from ragbot.infrastructure.events.redis_streams_bus import RedisStreamsEventBus


class TestRecoverPendingMessages:
    """Verify recover_pending_messages method exists with correct signature."""

    def test_method_exists(self) -> None:
        assert hasattr(RedisStreamsEventBus, "recover_pending_messages")

    def test_method_is_async(self) -> None:
        assert inspect.iscoroutinefunction(RedisStreamsEventBus.recover_pending_messages)

    def test_method_signature(self) -> None:
        sig = inspect.signature(RedisStreamsEventBus.recover_pending_messages)
        params = list(sig.parameters.keys())
        assert "stream" in params
        assert "group" in params
        assert "consumer" in params
        assert "min_idle_ms" in params
        assert "count" in params

    def test_default_min_idle_ms(self) -> None:
        sig = inspect.signature(RedisStreamsEventBus.recover_pending_messages)
        assert sig.parameters["min_idle_ms"].default == 30_000

    def test_default_count(self) -> None:
        sig = inspect.signature(RedisStreamsEventBus.recover_pending_messages)
        assert sig.parameters["count"].default == 10

    @pytest.mark.asyncio
    async def test_returns_zero_on_no_redis(self) -> None:
        """When xpending_range raises, method returns 0."""

        class _FakeRedis:
            async def xpending_range(self, *a, **kw):  # noqa: ANN
                raise ConnectionError("no redis")

        bus = RedisStreamsEventBus(client=_FakeRedis())  # type: ignore[arg-type]
        result = await bus.recover_pending_messages(
            stream="test:stream", group="test-group", consumer="c1",
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_empty_pending(self) -> None:
        """When no pending messages, returns 0."""

        class _FakeRedis:
            async def xpending_range(self, *a, **kw):  # noqa: ANN
                return []

        bus = RedisStreamsEventBus(client=_FakeRedis())  # type: ignore[arg-type]
        result = await bus.recover_pending_messages(
            stream="test:stream", group="test-group", consumer="c1",
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_claims_pending_messages(self) -> None:
        """When pending messages exist, xclaim is called and count returned."""

        class _FakeRedis:
            async def xpending_range(self, *a, **kw):  # noqa: ANN
                return [
                    {"message_id": b"1-0"},
                    {"message_id": b"2-0"},
                ]

            async def xclaim(self, *a, **kw):  # noqa: ANN
                return [
                    (b"1-0", {b"payload": b"{}"}),
                    (b"2-0", {b"payload": b"{}"}),
                ]

        bus = RedisStreamsEventBus(client=_FakeRedis())  # type: ignore[arg-type]
        result = await bus.recover_pending_messages(
            stream="test:stream", group="test-group", consumer="c1",
        )
        assert result == 2
