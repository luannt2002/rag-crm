"""Outbox repository port + publisher port."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from ragbot.shared.constants import (
    DEFAULT_OUTBOX_POLL_LIMIT,
    DEFAULT_OUTBOX_PUBLISH_BATCH_SIZE,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: UUID
    subject: str
    payload: bytes
    headers: dict[str, str]
    trace_id: str
    # Always the internal UUID PK of tenants.id, never an upstream INT.
    record_tenant_id: UUID
    created_at: datetime
    processed_at: datetime | None
    retry_count: int
    status: str  # pending | processed | dlq
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OutboxRepositoryPort(Protocol):
    async def poll_unprocessed(
        self, *, limit: int = DEFAULT_OUTBOX_POLL_LIMIT,
    ) -> Sequence[OutboxRecord]: ...

    async def mark_processed(self, ids: Sequence[UUID]) -> None: ...

    async def mark_retry(self, record_id: UUID, *, error: str) -> None: ...

    async def mark_dlq(self, record_id: UUID, *, reason: str) -> None: ...

    # Exactly-once helpers — caller owns session/tx so the FOR UPDATE
    # SKIP LOCKED row lock survives until publish + mark_processed commit
    # in the same transaction.
    def poll_one_for_update(
        self,
    ) -> AbstractAsyncContextManager[tuple[AsyncSession, OutboxRecord | None]]: ...

    async def mark_processed_in_session(
        self,
        session: AsyncSession,
        record_id: UUID,
        *,
        redis_entry_id: str = "",
    ) -> None: ...

    async def mark_retry_in_session(
        self, session: AsyncSession, record_id: UUID, *, error: str,
    ) -> None: ...

    async def mark_dlq_in_session(
        self, session: AsyncSession, record_id: UUID, *, reason: str,
    ) -> None: ...


@runtime_checkable
class OutboxPublisherPort(Protocol):
    async def publish_pending(
        self, *, batch_size: int = DEFAULT_OUTBOX_PUBLISH_BATCH_SIZE,
    ) -> int: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


__all__ = ["OutboxPublisherPort", "OutboxRecord", "OutboxRepositoryPort"]
