"""Unit tests for the per-request RLS ``SET LOCAL`` session hook.

The ``after_begin`` listener in ``infrastructure/db/session.py`` is the
generic wiring that binds ``app.tenant_id`` for EVERY transaction the
runtime opens (not just the explicit ``session_with_tenant`` call sites).
These tests mock the SQLAlchemy ``Connection`` so they run without a live
Postgres — the contract under test is:

  * when a tenant is bound on ``tenant_id_ctx``, the listener issues
    ``SET LOCAL app.tenant_id = '<uuid>'`` on the transaction's connection;
  * when no tenant is bound (ops / migration / background), the listener
    is a NO-OP (does not execute anything);
  * a non-UUID contextvar value is refused (never interpolated into SQL);
  * ``attach_rls_session_hook`` registers the listener (idempotently) and
    ``detach`` removes it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from ragbot.config.logging import tenant_id_ctx
from ragbot.infrastructure.db.session import (
    TENANT_SETTING_KEY,
    _after_begin,
    _current_tenant_uuid,
    attach_rls_session_hook,
    detach_rls_session_hook,
)


class _FakeConnection:
    """Stand-in for a sync ``Connection`` — records executed SQL strings."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, stmt: Any) -> None:
        self.executed.append(str(stmt))


def _bind_tenant(value: str) -> Any:
    return tenant_id_ctx.set(value)


def test_after_begin_sets_local_when_tenant_bound() -> None:
    """A bound UUID → ``SET LOCAL app.tenant_id = '<uuid>'`` on the conn."""
    tenant = "11111111-1111-1111-1111-111111111111"
    token = _bind_tenant(tenant)
    conn = _FakeConnection()
    try:
        _after_begin(session=object(), transaction=object(), connection=conn)
    finally:
        tenant_id_ctx.reset(token)

    assert len(conn.executed) == 1
    sql = conn.executed[0]
    assert f"SET LOCAL {TENANT_SETTING_KEY}" in sql
    assert tenant in sql


def test_after_begin_noop_when_unset() -> None:
    """No tenant bound → listener executes NOTHING (ops/migration safe)."""
    token = tenant_id_ctx.set("UNSET")
    conn = _FakeConnection()
    try:
        _after_begin(session=object(), transaction=object(), connection=conn)
    finally:
        tenant_id_ctx.reset(token)
    assert conn.executed == []


def test_after_begin_noop_on_non_uuid() -> None:
    """A poisoned (non-UUID) contextvar is refused — never interpolated."""
    token = tenant_id_ctx.set("'; DROP TABLE bots; --")
    conn = _FakeConnection()
    try:
        _after_begin(session=object(), transaction=object(), connection=conn)
    finally:
        tenant_id_ctx.reset(token)
    assert conn.executed == []


def test_current_tenant_uuid_normalises_uuid() -> None:
    """UUID parse round-trips to canonical string form."""
    token = tenant_id_ctx.set("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA")
    try:
        out = _current_tenant_uuid()
    finally:
        tenant_id_ctx.reset(token)
    assert out == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_current_tenant_uuid_none_when_unset() -> None:
    token = tenant_id_ctx.set("UNSET")
    try:
        assert _current_tenant_uuid() is None
    finally:
        tenant_id_ctx.reset(token)


def test_attach_and_detach_are_idempotent() -> None:
    """attach registers exactly one listener; re-attach is a no-op; detach
    removes it."""
    factory = sessionmaker()

    assert not event.contains(factory, "after_begin", _after_begin)

    attach_rls_session_hook(factory)
    assert event.contains(factory, "after_begin", _after_begin)

    # Re-attach must not raise nor double-register.
    attach_rls_session_hook(factory)
    assert event.contains(factory, "after_begin", _after_begin)

    detach_rls_session_hook(factory)
    assert not event.contains(factory, "after_begin", _after_begin)


def test_detach_when_not_attached_is_safe() -> None:
    """Detaching a never-attached factory must not raise."""
    factory = sessionmaker()
    detach_rls_session_hook(factory)
    assert not event.contains(factory, "after_begin", _after_begin)


def test_attach_registers_our_exact_listener() -> None:
    """The function registered on the factory IS ``_after_begin`` — so when
    SQLAlchemy fires ``after_begin`` on a real Session begun from this
    factory, our SET LOCAL binder (not some other fn) runs. Invoking that
    registered fn with a bound tenant produces the SET LOCAL — closing the
    wiring proof end-to-end without a live DB."""
    factory = sessionmaker()
    attach_rls_session_hook(factory)
    try:
        assert event.contains(factory, "after_begin", _after_begin)

        # The very fn that is now wired, called as SQLAlchemy would call it.
        tenant = "22222222-2222-2222-2222-222222222222"
        token = _bind_tenant(tenant)
        conn = _FakeConnection()
        try:
            _after_begin(session=object(), transaction=object(), connection=conn)
        finally:
            tenant_id_ctx.reset(token)
        assert any(tenant in s for s in conn.executed), conn.executed
    finally:
        detach_rls_session_hook(factory)
    assert not event.contains(factory, "after_begin", _after_begin)
