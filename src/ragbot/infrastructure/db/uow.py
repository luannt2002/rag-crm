"""SqlAlchemyUnitOfWork — transactional boundary + outbox.

Ref: PLAN_11 §uow.py / RAGBOT_MASTER §14.3.
"""

from __future__ import annotations

from uuid import uuid4

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.ports.bus_port import EventBusPort
from ragbot.config.logging import tenant_id_ctx
from ragbot.domain.events.base import DomainEvent
from ragbot.infrastructure.db.engine import _assert_uuid_str
from ragbot.infrastructure.db.models import OutboxModel
from ragbot.shared.constants import WORKSPACE_SYSTEM_SLUG
from ragbot.shared.errors import RepositoryError


class SqlAlchemyUnitOfWork:
    """Async UoW. Use as: `async with uow_factory() as uow: ...`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._outbox: list[OutboxModel] = []

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RepositoryError("UnitOfWork is not active — use `async with`")
        return self._session

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        # SECURITY: refuse to open a UoW if tenant context is not bound.
        # An empty / sentinel ContextVar value used to silently skip
        # ``SET LOCAL app.tenant_id`` so worker writes bypassed RLS — that
        # is a cross-tenant write leak. Callers MUST bind context first
        # via middleware, ``bind_request_context()``, or
        # ``session_with_tenant(..., record_tenant_id=...)``.
        tid = tenant_id_ctx.get()
        if not tid or tid == "UNSET":
            raise RuntimeError(
                "tenant_id_ctx not bound — call bind_request_context() "
                "before opening a SqlAlchemyUnitOfWork",
            )
        self._session = self._session_factory()
        # SET LOCAL can't take bind parameters; validate as UUID before
        # interpolation so the f-string is safe.
        safe_tid = _assert_uuid_str(tid)
        await self._session.execute(text(f"SET LOCAL app.tenant_id = '{safe_tid}'"))
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_val: object,
        exc_tb: object,
    ) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def commit(self) -> None:
        if self._session is None:
            raise RepositoryError("UoW not active")
        # Flush outbox rows first (in same transaction)
        for row in self._outbox:
            self._session.add(row)
        self._outbox.clear()
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is not None:
            await self._session.rollback()
        self._outbox.clear()

    async def add_outbox(self, event: DomainEvent) -> None:
        def _default(obj: object) -> str:  # noqa: ANN001
            return str(obj)

        payload = orjson.dumps(event.to_dict(), default=_default)
        # Bot-scoped events expose ``workspace_id`` on the dataclass; tenant-
        # level events fall back to the literal slug used by the schema
        # backfill so the NOT NULL column is always satisfied.
        workspace_slug = str(getattr(event, "workspace_id", "") or WORKSPACE_SYSTEM_SLUG)
        self._outbox.append(
            OutboxModel(
                subject=event.subject,
                payload=payload,
                headers={"trace-id": event.trace_id, "event-type": event.event_type},
                trace_id=event.trace_id,
                record_tenant_id=event.record_tenant_id,
                workspace_id=workspace_slug,
                metadata_json={"event_type": event.event_type},
            ),
        )

    async def add_outbox_raw(
        self,
        *,
        subject: str,
        payload: dict,
        tenant_id: object | None = None,
        workspace_id: str | None = None,
        trace_id: str = "",
    ) -> None:
        """Enqueue an outbox row without a full DomainEvent instance.

        Used for lightweight cross-replica invalidation events where we
        don't need the DomainEvent contract (e.g. registry cache busts).
        ``workspace_id`` defaults to the tenant-level system slug when the
        caller omits it — matches the migration backfill for forensic /
        cross-replica rows that are not 1:1 with a single bot.
        """
        ws = workspace_id or WORKSPACE_SYSTEM_SLUG
        self._outbox.append(
            OutboxModel(
                id=uuid4(),
                subject=subject,
                payload=orjson.dumps(payload),
                headers={"trace-id": trace_id, "event-type": subject},
                trace_id=trace_id,
                record_tenant_id=tenant_id,  # type: ignore[arg-type]
                workspace_id=ws,
                metadata_json={"event_type": subject},
            ),
        )


class UnitOfWorkFactory:
    """Callable returning new SqlAlchemyUnitOfWork."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)


__all__ = ["SqlAlchemyUnitOfWork", "UnitOfWorkFactory"]
