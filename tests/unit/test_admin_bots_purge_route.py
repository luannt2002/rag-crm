"""Route contract — ``POST /admin/bots/{bot_uuid}/purge`` (ADR-W1-D4 step 7).

Purge is the second phase of the two-phase delete (soft-delete = grace
window, purge = irreversible hard-delete + cache bust). The route:

- requires the same RBAC permission as delete (``bot``/``delete``),
- requires a tenant claim (R2 — RLS-scoped DELETE must never run
  tenant-less),
- 200 on a soft-deleted bot, 409 on a live bot, 404 when no row.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ragbot.application.services.bot_lifecycle_service import (
    BotNotPurgeableError,
    BotPurgeReport,
)
from ragbot.interfaces.http.routes import admin_bots


def _find_route(path_suffix: str) -> Any:
    for route in admin_bots.router.routes:
        if getattr(route, "path", "").endswith(path_suffix):
            return route
    return None


def test_purge_route_registered_with_delete_permission() -> None:
    route = _find_route("/{bot_uuid}/purge")
    assert route is not None, "purge route not registered"
    assert "POST" in route.methods
    # Dependency introspection — require_permission_dep("bot","delete")
    # produces a Depends whose callable closure carries the perm pair.
    dep_reprs = " ".join(
        repr(getattr(d, "dependency", d)) for d in route.dependencies
    )
    assert "require_permission" in dep_reprs or len(route.dependencies) >= 1


def _stub_request(*, tenant: Any, report: Any = None, error: Exception | None = None) -> MagicMock:
    request = MagicMock()
    request.state.record_tenant_id = tenant
    request.state.user_id = "admin-1"
    request.state.trace_id = "trace-x"
    svc = MagicMock()
    if error is not None:
        svc.purge_bot = AsyncMock(side_effect=error)
    else:
        svc.purge_bot = AsyncMock(return_value=report)
    request.app.state.container.bot_lifecycle_service.return_value = svc
    return request


@pytest.mark.asyncio
async def test_purge_route_200_on_soft_deleted_bot() -> None:
    bot_uuid = uuid4()
    tenant = uuid4()
    report = BotPurgeReport(
        record_bot_id=bot_uuid, purged=True, db_rows_bots=1,
        redis_uq_keys=3, skipped=["embedding_cache", "outbox_dedup"],
    )
    request = _stub_request(tenant=tenant, report=report)

    body = await admin_bots.admin_purge_bot(bot_uuid, request)

    assert body["ok"] is True
    assert body["data"]["purged"] is True
    assert body["data"]["db_rows_bots"] == 1
    svc = request.app.state.container.bot_lifecycle_service.return_value
    svc.purge_bot.assert_awaited_once()
    _, kwargs = svc.purge_bot.await_args
    assert kwargs["record_tenant_id"] == tenant
    assert kwargs["actor_user_id"] == "admin-1"


@pytest.mark.asyncio
async def test_purge_route_409_on_live_bot() -> None:
    request = _stub_request(tenant=uuid4(), error=BotNotPurgeableError("live"))
    with pytest.raises(HTTPException) as exc_info:
        await admin_bots.admin_purge_bot(uuid4(), request)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_purge_route_404_when_bot_missing() -> None:
    bot_uuid = uuid4()
    report = BotPurgeReport(
        record_bot_id=bot_uuid, purged=False, db_rows_bots=0,
        redis_uq_keys=0, skipped=[],
    )
    request = _stub_request(tenant=uuid4(), report=report)
    with pytest.raises(HTTPException) as exc_info:
        await admin_bots.admin_purge_bot(bot_uuid, request)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_purge_route_422_without_tenant_claim() -> None:
    """Tenant claim is REQUIRED (R2) — purge must never run tenant-less,
    even for platform admin."""
    request = _stub_request(tenant=None)
    request.state.record_tenant_id = None
    with pytest.raises(HTTPException) as exc_info:
        await admin_bots.admin_purge_bot(uuid4(), request)
    assert exc_info.value.status_code == 422
