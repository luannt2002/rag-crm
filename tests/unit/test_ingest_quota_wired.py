"""Ingest-quota gate is wired into the real upload routes (P2-H 🐛 IQ-1).

Before ADR-W2-D2 the gate was an orphan: ``IngestQuotaService`` existed but
``Container`` did not provide it and neither production upload route called
it (only the demo route did). These tests pin the wiring:

1. ``Container.ingest_quota_service`` provider exists.
2. The shared ``enforce_ingest_quota`` helper runs the atomic check inside
   a tenant-scoped session and propagates ``QuotaExceeded`` (→ 429).
3. Both upload route modules reference the helper (source-level guard so a
   refactor cannot silently drop the gate again — the exact IQ-1 regression).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def test_container_provides_ingest_quota_service() -> None:
    from ragbot.application.services.ingest_quota_service import IngestQuotaService
    from ragbot.bootstrap import Container

    assert Container.ingest_quota_service.cls is IngestQuotaService


@pytest.mark.asyncio
async def test_enforce_ingest_quota_charges_and_returns_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragbot.interfaces.http import _ingest_quota_guard as guard

    svc = MagicMock()
    svc.check_and_increment = AsyncMock(return_value=(3, 10))

    session = AsyncMock()
    # session_with_tenant is an async context manager yielding the session.
    class _CtxMgr:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        guard, "session_with_tenant", lambda *a, **k: _CtxMgr(),
    )
    container = SimpleNamespace(
        ingest_quota_service=lambda: svc,
        session_factory=lambda: object(),
    )
    tenant = uuid4()

    count, limit = await guard.enforce_ingest_quota(
        container, record_tenant_id=tenant, workspace_id="ws-1", increment_by=3,
    )

    assert (count, limit) == (3, 10)
    svc.check_and_increment.assert_awaited_once()
    _, kwargs = svc.check_and_increment.call_args
    assert kwargs["record_tenant_id"] == tenant
    assert kwargs["increment_by"] == 3
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_ingest_quota_propagates_quota_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragbot.interfaces.http import _ingest_quota_guard as guard
    from ragbot.shared.errors import QuotaExceeded

    svc = MagicMock()
    svc.check_and_increment = AsyncMock(side_effect=QuotaExceeded("cap"))
    session = AsyncMock()

    class _CtxMgr:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        guard, "session_with_tenant", lambda *a, **k: _CtxMgr(),
    )
    container = SimpleNamespace(
        ingest_quota_service=lambda: svc,
        session_factory=lambda: object(),
    )

    with pytest.raises(QuotaExceeded):
        await guard.enforce_ingest_quota(
            container, record_tenant_id=uuid4(), workspace_id="ws-1",
        )


def test_both_upload_routes_call_the_quota_guard() -> None:
    import ragbot.interfaces.http.routes.documents as docs
    import ragbot.interfaces.http.routes.documents_stream_upload as stream

    for module in (docs, stream):
        src = inspect.getsource(module)
        assert "enforce_ingest_quota" in src, (
            f"{module.__name__} must call enforce_ingest_quota before "
            "queuing ingest — otherwise the IQ-1 orphan reopens"
        )
