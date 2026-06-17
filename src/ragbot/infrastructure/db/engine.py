"""Async SQLAlchemy engine + session factory + tenant session var.

Ref: PLAN_11 §postgres.py / RAGBOT_MASTER §28.1.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import structlog
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ragbot.config.logging import tenant_id_ctx
from ragbot.config.settings import Settings
from ragbot.shared.constants import (
    DEFAULT_STATEMENT_TIMEOUT_MS,
    RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
    RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE,
)

_log = structlog.get_logger(__name__)


def _assert_uuid_str(value: object) -> str:
    """Narrow a contextvar value to a valid UUID string for SQL interpolation.

    SET LOCAL does not accept bind parameters, so we interpolate. Guard
    against misuse by validating the value parses as UUID first (P17 P2-1).
    """
    s = str(value)
    UUID(s)  # raises ValueError if not a valid UUID
    return s


def create_engine(settings: Settings) -> AsyncEngine:
    """Build an engine bound to the admin DSN (alembic / ops scripts)."""
    return create_async_engine(
        str(settings.database.url),
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_recycle=settings.database.pool_recycle,
        pool_timeout=settings.database.pool_timeout,
        pool_pre_ping=settings.database.pool_pre_ping,
        echo=settings.database.echo,
        future=True,
        # Note: statement_timeout NOT set globally — bulk ingestion needs >30s.
        # Use SET LOCAL statement_timeout in query-path sessions instead.
    )


def create_engine_app(settings: Settings) -> AsyncEngine:
    """Build an engine bound to the runtime application DSN.

    Falls back to the admin DSN only when the operator has explicitly
    opted in via the escape env var. The fallback emits a structured
    WARNING so RLS-bypass is observable in logs.
    """
    dsn = settings.database.url_app
    if dsn is None:
        escape = os.getenv(RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV, "").strip()
        if escape != RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE:
            msg = (
                "DATABASE_URL_APP is unset and the superuser runtime "
                "escape env is not active. Refusing to build runtime "
                "engine."
            )
            raise RuntimeError(msg)
        _log.warning(
            "engine.app_dsn_superuser_fallback",
            escape_env=RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
        )
        dsn = settings.database.url

    return create_async_engine(
        str(dsn),
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_recycle=settings.database.pool_recycle,
        pool_timeout=settings.database.pool_timeout,
        pool_pre_ping=settings.database.pool_pre_ping,
        echo=settings.database.echo,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


@asynccontextmanager
async def session_with_tenant(
    factory: async_sessionmaker[AsyncSession],
    *,
    record_tenant_id: object | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open session with `SET LOCAL app.tenant_id` for RLS.

    SECURITY: this helper REQUIRES a bound tenant — either via the
    ``record_tenant_id`` keyword (preferred — explicit at call site) or via
    a previously-set ``tenant_id_ctx`` (e.g. set by HTTP middleware /
    ``bind_request_context()``). If neither is available we raise
    ``RuntimeError`` rather than silently skipping ``SET LOCAL`` and letting
    a worker write rows that bypass RLS (cross-tenant write leak).

    IMPORTANT: SET LOCAL applies within the current implicit transaction.
    If caller calls session.commit() mid-session, the LOCAL settings are
    cleared. Use this context as a single-transaction unit.
    """
    if record_tenant_id is not None:
        tid_str = str(record_tenant_id)
        token = tenant_id_ctx.set(tid_str)
    else:
        tid_str = tenant_id_ctx.get()
        token = None

    if not tid_str or tid_str == "UNSET":
        if token is not None:
            tenant_id_ctx.reset(token)
        raise RuntimeError(
            "tenant_id_ctx not bound — call bind_request_context() or pass "
            "record_tenant_id= before opening a tenant-scoped session",
        )

    session = factory()
    try:
        # SET LOCAL does not support parameterized queries in PostgreSQL —
        # must interpolate. tid comes from our middleware as UUID, but
        # we validate shape here anyway as defense-in-depth (P17 P2-1).
        safe_tid = _assert_uuid_str(tid_str)
        await session.execute(text(f"SET LOCAL app.tenant_id = '{safe_tid}'"))
        # Workspace dimension (0141 policies) — explicit belt alongside the
        # after_begin hook, for callers that pass a non-hooked factory.
        # Slug shape re-validated before interpolation (SET LOCAL takes no
        # bind params); unbound/invalid → tenant-only semantics.
        from ragbot.infrastructure.db.session import (  # noqa: PLC0415 — avoid module cycle
            WORKSPACE_SETTING_KEY,
            _current_workspace_slug,
        )
        ws_slug = _current_workspace_slug()
        if ws_slug is not None:
            await session.execute(
                text(f"SET LOCAL {WORKSPACE_SETTING_KEY} = '{ws_slug}'"),
            )
        # Query-path timeout — bulk ingestion uses its own sessions
        await session.execute(text(f"SET LOCAL statement_timeout = '{DEFAULT_STATEMENT_TIMEOUT_MS}'"))
        yield session
    finally:
        await session.close()
        if token is not None:
            tenant_id_ctx.reset(token)


# Optional: PG instrumentation hook (lightweight)
def _on_engine_connect(dbapi_connection: object, _: object) -> None:  # pragma: no cover
    """Enable per-connection tunings (e.g. statement_timeout)."""
    cur = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cur.execute("SET application_name = 'ragbot'")
    finally:
        cur.close()


def attach_engine_hooks(engine: AsyncEngine) -> None:  # pragma: no cover
    event.listen(engine.sync_engine, "connect", _on_engine_connect)


__all__ = [
    "attach_engine_hooks",
    "create_engine",
    "create_engine_app",
    "create_session_factory",
    "dispose_engine",
    "session_with_tenant",
]
