"""Phase 1 regression — outbox publish XADD durability verify.

Case study ``reports/UPLOAD_FLOW_CASE_STUDY_20260516.md``:
``publish_raw`` previously returned ``None`` and threw ``BusError``
only on transport failure. A Redis blip between XADD and the outbox
row's ``mark_processed`` update silently marked the row processed
while no entry existed on the Stream (evidence: outbox row
``4248e92a`` processed 16:12:14, XLEN=0 at the same time).

These tests pin the new contract:

1. ``publish_raw`` / ``publish`` return a non-empty entry id on success.
2. XADD raising raises ``BusError`` — caller (publisher) rolls back its
   lock-tx, leaving the row pending for retry.
3. XADD returning falsy raises ``BusError`` — defence-in-depth against
   future redis-py behaviour returning ``None`` for a refused write.
4. The publisher threads the entry id into
   ``mark_processed_in_session(..., redis_entry_id=entry_id)``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from redis.exceptions import RedisError

from ragbot.infrastructure.events.redis_streams_bus import RedisStreamsEventBus
from ragbot.shared.errors import BusError


class _FakeRedis:
    """Minimal redis stand-in — ``xadd`` returns a controllable value."""

    def __init__(self, *, xadd_return: Any = b"1700000000000-0", raise_on_xadd: Exception | None = None) -> None:
        self._xadd_return = xadd_return
        self._raise = raise_on_xadd
        self.xadd_calls: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, key: str, data: dict[str, Any], **_kw: Any) -> Any:
        self.xadd_calls.append((key, data))
        if self._raise is not None:
            raise self._raise
        return self._xadd_return


@pytest.mark.asyncio
async def test_publish_raw_returns_entry_id_on_success() -> None:
    fake = _FakeRedis(xadd_return=b"1715840000000-0")
    bus = RedisStreamsEventBus(fake, stream_prefix="ragbot_test")

    entry_id = await bus.publish_raw(
        "document.uploaded.v1", b'{"doc": 1}',
        headers={"X-Trace": "t-1"}, msg_id="outbox-1",
    )

    assert isinstance(entry_id, str) and entry_id == "1715840000000-0"
    assert fake.xadd_calls and fake.xadd_calls[0][0] == "ragbot_test:document.uploaded.v1"


@pytest.mark.asyncio
async def test_publish_raw_string_entry_id_returned_as_is() -> None:
    """Some redis-py versions return ``str`` not ``bytes``. Either path
    must yield a non-empty ``str`` so the outbox row can persist it."""
    fake = _FakeRedis(xadd_return="1715840000000-7")
    bus = RedisStreamsEventBus(fake, stream_prefix="ragbot_test")

    entry_id = await bus.publish_raw("document.uploaded.v1", b"{}")

    assert entry_id == "1715840000000-7"


@pytest.mark.asyncio
async def test_publish_raw_raises_bus_error_on_xadd_redis_failure() -> None:
    fake = _FakeRedis(raise_on_xadd=RedisError("network reset"))
    bus = RedisStreamsEventBus(fake, stream_prefix="ragbot_test")

    with pytest.raises(BusError) as exc_info:
        await bus.publish_raw("document.uploaded.v1", b"{}")

    assert "xadd transport failure" in str(exc_info.value)


@pytest.mark.asyncio
async def test_publish_raw_raises_bus_error_on_xadd_timeout() -> None:
    fake = _FakeRedis(raise_on_xadd=asyncio.TimeoutError())
    bus = RedisStreamsEventBus(fake, stream_prefix="ragbot_test")

    with pytest.raises(BusError):
        await bus.publish_raw("document.uploaded.v1", b"{}")


@pytest.mark.asyncio
async def test_publish_raw_raises_bus_error_on_falsy_entry_id() -> None:
    """Defence-in-depth: if a future redis-py returns ``None`` for a
    silently-refused write, treat it as a publish failure so the row
    stays pending."""
    fake = _FakeRedis(xadd_return=None)
    bus = RedisStreamsEventBus(fake, stream_prefix="ragbot_test")

    with pytest.raises(BusError) as exc_info:
        await bus.publish_raw("document.uploaded.v1", b"{}")

    assert "empty entry_id" in str(exc_info.value)


@pytest.mark.asyncio
async def test_publish_raw_raises_bus_error_on_empty_string_entry_id() -> None:
    fake = _FakeRedis(xadd_return="")
    bus = RedisStreamsEventBus(fake, stream_prefix="ragbot_test")

    with pytest.raises(BusError):
        await bus.publish_raw("document.uploaded.v1", b"{}")


# ---------------------------------------------------------------------
# Publisher-loop integration — entry_id threaded into mark_processed.
# ---------------------------------------------------------------------


@dataclass
class _FakeRec:
    id: UUID = field(default_factory=uuid4)
    subject: str = "document.uploaded.v1"
    payload: bytes = b'{"doc": 1}'
    headers: dict[str, str] = field(default_factory=dict)
    trace_id: str = ""
    record_tenant_id: Any = None
    created_at: Any = None
    processed_at: Any = None
    retry_count: int = 0
    status: str = "pending"
    last_error: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class _CapturingBus:
    """Records the publish call and the entry id we hand back."""

    def __init__(self, *, entry_id: str = "fake-entry-1") -> None:
        self._entry_id = entry_id
        self.calls: list[tuple[str, bytes, dict[str, str], str]] = []

    async def publish_raw(
        self, subject: str, payload: bytes,
        *, headers: dict[str, str] | None = None, msg_id: str | None = None,
    ) -> str:
        self.calls.append((subject, payload, dict(headers or {}), msg_id or ""))
        return self._entry_id


@pytest.mark.asyncio
async def test_publish_one_returns_bus_entry_id() -> None:
    """``_publish_one`` must surface the bus-assigned entry id so the
    publisher loop can persist it onto the outbox row."""
    from ragbot.interfaces.workers.outbox_publisher import _publish_one

    bus = _CapturingBus(entry_id="42-0")
    rec = _FakeRec()
    entry_id = await _publish_one(bus=bus, rec=rec)  # type: ignore[arg-type]

    assert entry_id == "42-0"
    # Msg-Id header carries the stable outbox UUID for consumer dedup.
    assert bus.calls[0][2]["Msg-Id"] == str(rec.id)


@pytest.mark.asyncio
async def test_publish_one_returns_empty_string_for_non_str_result() -> None:
    """Bus implementations that return ``None`` (older test fakes)
    must be normalised to an empty string so the publisher can still
    write a NULL ``redis_entry_id`` without TypeError."""
    from ragbot.interfaces.workers.outbox_publisher import _publish_one

    class _LegacyBus:
        async def publish_raw(self, *_a: Any, **_kw: Any) -> None:
            return None

    rec = _FakeRec()
    entry_id = await _publish_one(bus=_LegacyBus(), rec=rec)  # type: ignore[arg-type]

    assert entry_id == ""
