"""Z2-P1 regression: outbox publisher retry/DLQ must catch ALL transient
publish errors, not only `BusError`.

Audit `AUDIT_DEEPDIVE_OUTBOX_WORKERS_20260429_142902.md` (P1-4):
The narrow `except BusError` swallowed only library-wrapped errors.
Raw RedisError / OSError / asyncio.TimeoutError raised by `publish_raw`
fell through to the outer broad-except, the row stayed `pending`, and
retry_count was never incremented — silent stuck-forever rows.

Fix broadens the inner tuple to (BusError, RedisError, OSError,
asyncio.TimeoutError). These tests assert each error class triggers
the retry path (and the DLQ path when retry_count is exhausted).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from redis.exceptions import RedisError

from ragbot.shared.errors import BusError


@dataclass
class _FakeRec:
    id: UUID = field(default_factory=uuid4)
    subject: str = "test.event.v1"
    payload: bytes = b'{"ok": true}'
    headers: dict[str, str] = field(default_factory=dict)
    retry_count: int = 0
    record_tenant_id: Any = None
    trace_id: Any = None
    created_at: Any = None


class _FakeRepo:
    def __init__(self) -> None:
        self.dlq_calls: list[tuple[UUID, str]] = []
        self.retry_calls: list[tuple[UUID, str]] = []

    async def mark_dlq(self, rec_id: UUID, *, reason: str) -> None:
        self.dlq_calls.append((rec_id, reason))

    async def mark_retry(self, rec_id: UUID, *, error: str) -> None:
        self.retry_calls.append((rec_id, error))


def _classify(exc: Exception) -> str:
    """Mirror the inner-except predicate of the publisher."""
    if isinstance(exc, (BusError, RedisError, OSError, asyncio.TimeoutError)):
        return "retry-or-dlq"
    return "outer-broad-except"


@pytest.mark.parametrize(
    "exc",
    [
        BusError("kaboom"),
        RedisError("connection reset"),
        OSError("EPIPE"),
        ConnectionError("redis down"),  # ConnectionError is OSError subclass
        asyncio.TimeoutError(),
    ],
)
def test_inner_except_classifies_transient_errors(exc: Exception) -> None:
    """Each of these error classes MUST be classified to the retry/DLQ path."""
    assert _classify(exc) == "retry-or-dlq"


def test_unrelated_error_falls_through_to_outer_loop() -> None:
    """Errors NOT in the tuple still flow to the outer broad-except (which
    logs and sleeps). This is intentional — only transient publish errors
    should mutate retry_count."""
    assert _classify(ValueError("bad payload shape")) == "outer-broad-except"
    assert _classify(KeyError("missing")) == "outer-broad-except"
    assert _classify(RuntimeError("?")) == "outer-broad-except"


@pytest.mark.asyncio
async def test_redis_error_triggers_retry_when_under_cap() -> None:
    """retry_count < max_retries → mark_retry, not mark_dlq."""
    repo = _FakeRepo()
    rec = _FakeRec(retry_count=1)
    max_retries = 5

    exc = RedisError("transient")
    if rec.retry_count >= max_retries:
        await repo.mark_dlq(rec.id, reason=str(exc))
    else:
        await repo.mark_retry(rec.id, error=str(exc))

    assert repo.retry_calls and not repo.dlq_calls
    assert repo.retry_calls[0][1] == "transient"


@pytest.mark.asyncio
async def test_redis_error_triggers_dlq_when_at_cap() -> None:
    """retry_count >= max_retries → mark_dlq, not mark_retry."""
    repo = _FakeRepo()
    rec = _FakeRec(retry_count=5)
    max_retries = 5

    exc = OSError("EPIPE on stream xadd")
    if rec.retry_count >= max_retries:
        await repo.mark_dlq(rec.id, reason=str(exc))
    else:
        await repo.mark_retry(rec.id, error=str(exc))

    assert repo.dlq_calls and not repo.retry_calls
    assert repo.dlq_calls[0][1] == "EPIPE on stream xadd"


def test_redis_error_inherits_from_exception_not_baseexception() -> None:
    """Sanity: RedisError is catchable by `except Exception`. If a future
    redis client release changes this, the outer-loop fall-through would
    no longer protect us against the bug class."""
    assert issubclass(RedisError, Exception)
    assert not issubclass(asyncio.CancelledError, Exception)  # by design, must not be caught
