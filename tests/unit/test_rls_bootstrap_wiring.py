"""Bootstrap wires the RLS session hook (ADR-W1-D3 piece 1).

``attach_rls_session_hook`` had zero production callsites (P2-C RLS-2) —
the generic SET LOCAL binder existed but nothing attached it, so bare
``session_factory`` sessions never bound the tenant GUC. The fix routes the
container's ``session_factory`` provider through
``create_rls_session_factory`` (build factory → attach hook → return),
which is a no-op under the superuser DSN and bites the moment ops flips
``DATABASE_URL_APP`` to the NOBYPASSRLS role.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker


def test_create_rls_session_factory_attaches_hook(monkeypatch: Any) -> None:
    from ragbot.infrastructure.db import engine as engine_mod
    from ragbot.infrastructure.db.session import (
        _after_begin,
        create_rls_session_factory,
        detach_rls_session_hook,
    )

    plain_factory = sessionmaker()
    monkeypatch.setattr(
        engine_mod, "create_session_factory", lambda engine: plain_factory,
    )

    out = create_rls_session_factory(engine=MagicMock())

    try:
        assert out is plain_factory
        assert event.contains(plain_factory, "after_begin", _after_begin), (
            "create_rls_session_factory must attach the SET LOCAL binder "
            "to the factory it returns"
        )
    finally:
        detach_rls_session_hook(plain_factory)


def test_container_session_factory_routes_through_rls_wrapper() -> None:
    """The DI provider itself must point at the RLS-attaching wrapper —
    pinning the bootstrap wiring so a refactor cannot silently revert to
    the bare factory (the exact 0-callsite regression P2-C found)."""
    from ragbot.bootstrap import Container
    from ragbot.infrastructure.db.session import create_rls_session_factory

    assert Container.session_factory.provides is create_rls_session_factory, (
        "bootstrap session_factory provider must build via "
        "create_rls_session_factory (ADR-W1-D3)"
    )
