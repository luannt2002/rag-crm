"""Unit tests — RLS request/system role split (plan 260619-rls-enforcement).

Row-level security enforces only when the app connects as a NOBYPASSRLS role
(``ragbot_app``) AND the four trusted cross-tenant background workers — outbox
publisher, document recovery scan, semantic-cache GC, cost-cap aggregate — run
on a SEPARATE BYPASSRLS engine (``ragbot_system``). Otherwise those workers,
which have no single tenant context, would be fail-closed to zero rows: outbox
stuck, recovery blind, GC a no-op, alerter blind.

These tests pin the split contract so a future refactor cannot silently send a
cross-tenant worker back through the RLS-enforced request factory (which would
reintroduce the fail-closed regression) or let the migration role literals
drift from the code constants.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ragbot.infrastructure.db.engine import create_engine_system
from ragbot.infrastructure.db.session import RUNTIME_DB_ROLE, SYSTEM_DB_ROLE

_ALEMBIC = Path(__file__).resolve().parents[2] / "alembic" / "versions"


# ---------------------------------------------------------------------
# Migration role literals must match the code constants (drift guard).
# ---------------------------------------------------------------------


def _role_literal(filename: str, var: str) -> str:
    text = (_ALEMBIC / filename).read_text(encoding="utf-8")
    m = re.search(rf'{var}\s*=\s*"([^"]+)"', text)
    assert m is not None, f"{var} literal not found in {filename}"
    return m.group(1)


def test_app_role_migration_literal_matches_constant() -> None:
    """The NOBYPASSRLS request-role migration must grant exactly the role the
    runtime connects as — a divergence would leave RLS dead (grants on a role
    nobody uses)."""
    assert (
        _role_literal("20260619_rls_app_role_grants.py", "_APP_ROLE")
        == RUNTIME_DB_ROLE
        == "ragbot_app"
    )


def test_system_role_migration_literal_matches_constant() -> None:
    """The BYPASSRLS system-role migration must grant exactly the role the
    workers connect as."""
    assert (
        _role_literal("20260619_rls_system_role_grants.py", "_SYSTEM_ROLE")
        == SYSTEM_DB_ROLE
        == "ragbot_system"
    )


def test_app_and_system_roles_are_distinct() -> None:
    """The whole point of the split: the request role and the worker role are
    different DB principals (one NOBYPASSRLS, one BYPASSRLS)."""
    assert RUNTIME_DB_ROLE != SYSTEM_DB_ROLE


# ---------------------------------------------------------------------
# create_engine_system — DSN selection (dedicated role vs admin fallback).
# ---------------------------------------------------------------------


def _fake_settings(*, url_system: str | None, url: str) -> SimpleNamespace:
    return SimpleNamespace(
        database=SimpleNamespace(
            url_system=url_system,
            url=url,
            pool_size=1,
            max_overflow=0,
            pool_recycle=1800,
            pool_timeout=30,
            pool_pre_ping=True,
            echo=False,
        )
    )


@pytest.mark.asyncio
async def test_system_engine_uses_dedicated_dsn_when_set() -> None:
    """When DATABASE_URL_SYSTEM is set, the system engine binds the dedicated
    BYPASSRLS role — NOT the admin superuser."""
    settings = _fake_settings(
        url_system="postgresql+asyncpg://ragbot_system:x@localhost:5434/ragbot",
        url="postgresql+asyncpg://ragbot:x@localhost:5434/ragbot",
    )
    engine = create_engine_system(settings)
    try:
        assert engine.url.username == "ragbot_system"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_engine_falls_back_to_admin_when_unset() -> None:
    """Unset DATABASE_URL_SYSTEM → fall back to the admin DSN (also bypasses
    RLS), so the split is inert until ops provisions the dedicated role."""
    settings = _fake_settings(
        url_system=None,
        url="postgresql+asyncpg://ragbot:x@localhost:5434/ragbot",
    )
    engine = create_engine_system(settings)
    try:
        assert engine.url.username == "ragbot"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------
# DI wiring — outbox_repo (publisher) bound to the system factory.
# ---------------------------------------------------------------------


def test_outbox_repo_uses_system_factory_not_request_factory() -> None:
    """The outbox publisher drains cross-tenant; its repo MUST be wired to the
    BYPASSRLS system factory. A regression here = silent publish stall under
    the request role."""
    from ragbot.bootstrap import Container

    sf = Container.outbox_repo.kwargs["session_factory"]
    assert sf is Container.system_session_factory
    assert sf is not Container.session_factory


def test_request_repo_still_uses_request_factory() -> None:
    """Control: a normal per-request repo stays on the RLS-enforced request
    factory (the split is surgical, not a blanket swap)."""
    from ragbot.bootstrap import Container

    assert Container.bot_repo.kwargs["session_factory"] is Container.session_factory


# ---------------------------------------------------------------------
# Worker bodies open the SYSTEM factory, never the request factory.
# ---------------------------------------------------------------------


def _container_with_distinct_factories() -> MagicMock:
    """Container whose request factory RAISES if touched (so a worker that
    wrongly uses it fails the test loud) and whose system factory is benign."""
    container = MagicMock()
    container.session_factory.side_effect = AssertionError(
        "worker must NOT use the RLS-enforced request factory",
    )
    # system factory returns a callable that raises a *narrow* error the
    # worker's own except-tuple swallows, so the loop sleeps and we can cancel.
    container.system_session_factory.return_value = MagicMock(
        side_effect=RuntimeError("probe-stop"),
    )
    return container


@pytest.mark.asyncio
async def test_cost_cap_alerter_binds_system_factory() -> None:
    from ragbot.interfaces.http.embedded_workers import run_embedded_cost_cap_alerter

    container = _container_with_distinct_factories()
    task = asyncio.create_task(run_embedded_cost_cap_alerter(container))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    container.system_session_factory.assert_called_once()
    container.session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_cache_purge_binds_system_factory() -> None:
    from ragbot.interfaces.http.embedded_workers import run_embedded_cache_purge

    container = _container_with_distinct_factories()
    task = asyncio.create_task(run_embedded_cache_purge(container))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    container.system_session_factory.assert_called_once()
    container.session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_loop_binds_system_factory() -> None:
    """The stuck-doc scan must run on the BYPASSRLS factory; the per-doc replay
    keeps its own SET LOCAL tenant attribution downstream."""
    from ragbot.interfaces.workers.document_recovery_worker import run_recovery_loop

    container = MagicMock()
    container.session_factory.side_effect = AssertionError(
        "recovery scan must NOT use the request factory",
    )
    stop = asyncio.Event()
    stop.set()  # exit before the first sweep — we only assert the factory bind
    await run_recovery_loop(container, stop_event=stop)
    container.system_session_factory.assert_called_once()
    container.session_factory.assert_not_called()
