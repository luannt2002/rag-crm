"""``session_with_tenant`` helper unit tests.

Cross-tenant write leak fix: the helper MUST refuse to open a DB session
when the tenant context is not bound, and MUST set / reset the
``tenant_id_ctx`` ContextVar correctly so concurrent task isolation
holds.

These tests stub the session factory so they can run without a live
Postgres — the contract under test is the ContextVar manipulation +
RuntimeError gate, not the actual SQL.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import text as _sa_text  # noqa: F401  # used by stub assertion

from ragbot.config.logging import tenant_id_ctx
from ragbot.infrastructure.db.engine import session_with_tenant


class _FakeSession:
    """Stand-in for ``AsyncSession`` — records executed SQL strings."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.closed = False

    async def execute(self, stmt: Any, params: Any = None) -> None:  # noqa: ARG002
        # ``stmt`` is a SQLAlchemy ``text(...)`` clause — stringifying it
        # yields the SQL with placeholders intact, which is enough for the
        # SET LOCAL assertions we make below.
        self.executed.append(str(stmt))

    async def close(self) -> None:
        self.closed = True


def _factory_returning(session: _FakeSession) -> Any:
    """Return a callable that mimics ``async_sessionmaker`` returning a session."""

    def _make() -> _FakeSession:
        return session

    return _make


@pytest.mark.asyncio
async def test_session_sets_local_app_tenant_id() -> None:
    """When called with explicit ``record_tenant_id``, the helper executes
    ``SET LOCAL app.tenant_id`` with the provided UUID — proving RLS scoping
    is enforced at the session boundary."""
    fake = _FakeSession()
    tenant = "11111111-1111-1111-1111-111111111111"
    async with session_with_tenant(
        _factory_returning(fake), record_tenant_id=tenant,
    ) as session:
        assert session is fake

    # First execute = SET LOCAL app.tenant_id, then statement_timeout.
    assert any("SET LOCAL app.tenant_id" in s for s in fake.executed), fake.executed
    assert any(tenant in s for s in fake.executed), fake.executed
    assert fake.closed is True


@pytest.mark.asyncio
async def test_context_var_reset_on_exit() -> None:
    """The ContextVar token bound by the helper is reset when the context
    manager exits — leaking tenant_id between unrelated tasks would
    re-introduce the cross-tenant write leak we just patched."""
    # Snapshot — this should be the module default ("UNSET") at start.
    before = tenant_id_ctx.get()
    fake = _FakeSession()
    tenant = "22222222-2222-2222-2222-222222222222"
    async with session_with_tenant(
        _factory_returning(fake), record_tenant_id=tenant,
    ):
        # Inside the block, the ContextVar reflects the bound tenant.
        assert tenant_id_ctx.get() == tenant
    # On exit, the var is restored to its previous value.
    assert tenant_id_ctx.get() == before


@pytest.mark.asyncio
async def test_unset_tenant_ctx_raises_loud() -> None:
    """When neither the ContextVar nor the explicit kwarg is set, the
    helper raises ``RuntimeError`` rather than silently skipping
    ``SET LOCAL`` (which would let RLS be bypassed)."""
    fake = _FakeSession()
    # Belt-and-braces: clear the ContextVar to its default for this test.
    token = tenant_id_ctx.set("UNSET")
    try:
        with pytest.raises(RuntimeError, match="tenant_id_ctx not bound"):
            async with session_with_tenant(_factory_returning(fake)):
                pass  # pragma: no cover — must not enter body
    finally:
        tenant_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_concurrent_sessions_isolated() -> None:
    """Two concurrent tasks each binding their own tenant must NOT see
    each other's value — verifies ContextVar.copy_context semantics
    (asyncio.create_task copies context per-task)."""
    fake_a = _FakeSession()
    fake_b = _FakeSession()
    tenant_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    tenant_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    seen_a: list[str] = []
    seen_b: list[str] = []

    async def _task_a() -> None:
        async with session_with_tenant(
            _factory_returning(fake_a), record_tenant_id=tenant_a,
        ):
            await asyncio.sleep(0)  # yield so task_b interleaves
            seen_a.append(tenant_id_ctx.get())

    async def _task_b() -> None:
        async with session_with_tenant(
            _factory_returning(fake_b), record_tenant_id=tenant_b,
        ):
            await asyncio.sleep(0)
            seen_b.append(tenant_id_ctx.get())

    await asyncio.gather(_task_a(), _task_b())

    assert seen_a == [tenant_a]
    assert seen_b == [tenant_b]
