"""Outbox repository — poll, mark processed, mark DLQ.

Exactly-once design (Agent N, 2026-05-16): the legacy ``poll_unprocessed``
method opened a session, took ``FOR UPDATE SKIP LOCKED`` row locks, then
closed the session before returning. Closing released the locks, so two
publisher replicas could fetch the same row and double-publish.

The new ``poll_one_for_update`` returns the session via an async context
manager so the caller commits/rolls back the same tx that holds the lock,
making publish + mark_processed atomic with respect to other replicas.

``poll_unprocessed`` and the bulk mutators stay for backwards compatibility
(test fixtures + tooling), but the production publisher loop now uses the
per-row entry point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ragbot.application.ports.outbox_port import OutboxRecord, OutboxRepositoryPort
from ragbot.infrastructure.db.models import OutboxModel


class SqlAlchemyOutboxRepository(OutboxRepositoryPort):
    """Outbox poller-friendly repo. Uses FOR UPDATE SKIP LOCKED."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Khởi tạo repository với session factory."""
        self._session_factory = session_factory

    async def poll_unprocessed(self, *, limit: int = 100) -> Sequence[OutboxRecord]:
        """Lấy batch bản ghi outbox chưa xử lý.

        Legacy fan-out poll — opens a short session, snapshots the rows
        and returns. Row locks are NOT preserved across session close, so
        callers that need exactly-once semantics MUST use
        :meth:`poll_one_for_update` instead.

        @param limit: số bản ghi tối đa mỗi lần poll
        @return: danh sách OutboxRecord
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(OutboxModel)
                .where(OutboxModel.processed_at.is_(None), OutboxModel.status == "pending")
                .order_by(OutboxModel.created_at.asc())
                .limit(limit),
            )
            rows = list(result.scalars().all())
            return [self._to_record(r) for r in rows]

    @asynccontextmanager
    async def poll_one_for_update(
        self,
    ) -> AsyncIterator[tuple[AsyncSession, OutboxRecord | None]]:
        """Lock one pending row for the caller's tx (FOR UPDATE SKIP LOCKED).

        Yields ``(session, record_or_None)``. Caller MUST commit or
        rollback the session — the row lock is released on tx end.

        Concurrent publisher replicas calling this simultaneously each
        receive a different row (SKIP LOCKED skips rows held by peers)
        or ``None`` if the table is drained.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(OutboxModel)
                .where(
                    OutboxModel.processed_at.is_(None),
                    OutboxModel.status == "pending",
                )
                .order_by(OutboxModel.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True),
            )
            row = result.scalars().first()
            record = self._to_record(row) if row is not None else None
            try:
                yield session, record
            except BaseException:
                # On any caller exception, rollback so the row lock
                # releases and the row remains visible to the next poll.
                await session.rollback()
                raise

    async def mark_processed(self, ids: Sequence[UUID]) -> None:
        """Đánh dấu các bản ghi đã xử lý thành công.
        @param ids: danh sách UUID cần đánh dấu
        """
        if not ids:
            return
        async with self._session_factory() as session:
            await session.execute(
                update(OutboxModel)
                .where(OutboxModel.id.in_(ids))
                .values(
                    processed_at=datetime.now(tz=timezone.utc),
                    status="processed",
                ),
            )
            await session.commit()

    async def mark_retry(self, record_id: UUID, *, error: str) -> None:
        """Tăng retry_count và ghi lỗi cho bản ghi cần thử lại.
        @param record_id: UUID bản ghi
        @param error: mô tả lỗi
        """
        async with self._session_factory() as session:
            await session.execute(
                update(OutboxModel)
                .where(OutboxModel.id == record_id)
                .values(
                    retry_count=OutboxModel.retry_count + 1,
                    last_error=error,
                ),
            )
            await session.commit()

    async def mark_dlq(self, record_id: UUID, *, reason: str) -> None:
        """Chuyển bản ghi sang dead-letter queue.
        @param record_id: UUID bản ghi
        @param reason: lý do chuyển DLQ
        """
        async with self._session_factory() as session:
            await session.execute(
                update(OutboxModel)
                .where(OutboxModel.id == record_id)
                .values(status="dlq", last_error=reason),
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Per-tx mutators — called by the exactly-once publisher loop while
    # holding the row lock. Caller owns commit/rollback.
    # ------------------------------------------------------------------
    @staticmethod
    async def mark_processed_in_session(
        session: AsyncSession,
        record_id: UUID,
        *,
        redis_entry_id: str = "",
    ) -> None:
        """Mark a single row processed inside the caller's tx.

        ``redis_entry_id`` (alembic 010h) stores the Stream entry id the
        bus returned from XADD so operators can join an outbox row to the
        actual Redis Streams entry for forensic replay. Empty string is
        normalised to ``NULL`` — keeping legacy callers that did not
        supply it from writing a sentinel.
        """
        await session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == record_id)
            .values(
                processed_at=datetime.now(tz=timezone.utc),
                status="processed",
                redis_entry_id=redis_entry_id or None,
            ),
        )

    @staticmethod
    async def mark_retry_in_session(
        session: AsyncSession, record_id: UUID, *, error: str,
    ) -> None:
        """Increment retry_count + log error inside the caller's tx."""
        await session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == record_id)
            .values(
                retry_count=OutboxModel.retry_count + 1,
                last_error=error,
            ),
        )

    @staticmethod
    async def mark_dlq_in_session(
        session: AsyncSession, record_id: UUID, *, reason: str,
    ) -> None:
        """Move a single row to DLQ inside the caller's tx."""
        await session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == record_id)
            .values(status="dlq", last_error=reason),
        )

    @staticmethod
    def _to_record(r: OutboxModel) -> OutboxRecord:
        """Chuyển đổi ORM OutboxModel sang DTO OutboxRecord."""
        return OutboxRecord(
            id=cast(UUID, r.id),
            subject=r.subject,
            payload=bytes(r.payload),
            headers=dict(r.headers or {}),
            trace_id=r.trace_id or "",
            record_tenant_id=cast(UUID, r.record_tenant_id),
            created_at=r.created_at,
            processed_at=r.processed_at,
            retry_count=r.retry_count,
            status=r.status,
            last_error=r.last_error,
            metadata=dict(r.metadata_json or {}),
        )


__all__ = ["SqlAlchemyOutboxRepository"]
