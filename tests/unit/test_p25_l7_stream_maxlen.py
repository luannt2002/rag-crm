"""P25-L7: Redis Stream XADD MAXLEN to bound unbounded growth.

Verifies that `RedisStreamsEventBus.publish` and `publish_raw` both pass
`maxlen=DEFAULT_STREAM_MAXLEN, approximate=True` to `XADD` so that Redis
streams do not grow unbounded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ragbot.domain.events.base import DomainEvent
from ragbot.infrastructure.events.redis_streams_bus import RedisStreamsEventBus
from ragbot.shared.constants import DEFAULT_STREAM_MAXLEN


def _make_event() -> DomainEvent:
    """Build a minimal DomainEvent instance for publish() test."""
    return DomainEvent(
        occurred_at=datetime.now(tz=timezone.utc),
        record_tenant_id=uuid4(),
        trace_id=str(uuid4()),
    )


class TestStreamMaxlen:
    @pytest.mark.asyncio
    async def test_publish_passes_maxlen_to_xadd(self) -> None:
        """publish() must pass maxlen + approximate=True to XADD."""
        fake = AsyncMock()
        fake.xadd = AsyncMock(return_value=b"1-0")

        bus = RedisStreamsEventBus(client=fake)  # type: ignore[arg-type]
        event = _make_event()

        await bus.publish(event)

        assert fake.xadd.await_count == 1
        _args, kwargs = fake.xadd.await_args
        assert kwargs.get("maxlen") == DEFAULT_STREAM_MAXLEN
        assert kwargs.get("approximate") is True

    @pytest.mark.asyncio
    async def test_publish_raw_passes_maxlen_to_xadd(self) -> None:
        """publish_raw() must pass maxlen + approximate=True to XADD."""
        fake = AsyncMock()
        fake.xadd = AsyncMock(return_value=b"1-0")

        bus = RedisStreamsEventBus(client=fake)  # type: ignore[arg-type]

        await bus.publish_raw("chat.answered.v1", b'{"ok": true}')

        assert fake.xadd.await_count == 1
        _args, kwargs = fake.xadd.await_args
        assert kwargs.get("maxlen") == DEFAULT_STREAM_MAXLEN
        assert kwargs.get("approximate") is True

    def test_maxlen_constant_is_reasonable(self) -> None:
        """Sanity sniff: between 10k and 10M entries."""
        assert DEFAULT_STREAM_MAXLEN >= 10_000
        assert DEFAULT_STREAM_MAXLEN <= 10_000_000
