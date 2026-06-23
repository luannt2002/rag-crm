"""RBAC + wiring gate for the new usage-rollup admin endpoints (G-3).

  * ``/metrics/usage/rollup`` requires admin (level 60); an under-level caller
    (operator, level 40) gets ForbiddenError.
  * ``/metrics/usage/cross-tenant`` requires super-admin (level 100); an admin
    (level 60) is rejected.
  * a permitted caller reaches the repo and the route returns its rows.

Calls the route coroutines directly with a fake Request (no ASGI / TestClient)
so the fastapi route-introspection helper version mismatch is irrelevant here.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from ragbot.interfaces.http.routes.admin_metrics import (
    metrics_usage_cross_tenant,
    metrics_usage_rollup,
)
from ragbot.shared.errors import ForbiddenError


class _StubRepo:
    def __init__(self) -> None:
        self.rollup_called = False
        self.cross_called = False

    async def usage_rollup(self, **kwargs):
        self.rollup_called = True
        return [{"dim_key": "x", "cost_usd": 0.01}]

    async def cross_tenant_rollup(self, **kwargs):
        self.cross_called = True
        return [{"record_tenant_id": uuid4(), "cost_usd": 0.02}]


def _make_request(role: str, repo: _StubRepo) -> SimpleNamespace:
    container = SimpleNamespace(token_ledger_analytics_repo=lambda: repo)
    app = SimpleNamespace(state=SimpleNamespace(container=container))
    state = SimpleNamespace(role=role, record_tenant_id=uuid4())
    return SimpleNamespace(app=app, state=state)


@pytest.mark.asyncio
async def test_rollup_forbidden_for_operator():
    repo = _StubRepo()
    req = _make_request("operator", repo)  # level 40 < admin 60
    with pytest.raises(ForbiddenError):
        await metrics_usage_rollup(req)
    assert repo.rollup_called is False


@pytest.mark.asyncio
async def test_rollup_ok_for_admin():
    repo = _StubRepo()
    req = _make_request("admin", repo)  # level 60
    out = await metrics_usage_rollup(req, dim="bot")
    assert out["ok"] is True
    assert repo.rollup_called is True


@pytest.mark.asyncio
async def test_cross_tenant_forbidden_for_admin():
    repo = _StubRepo()
    req = _make_request("admin", repo)  # level 60 < super_admin 100
    with pytest.raises(ForbiddenError):
        await metrics_usage_cross_tenant(req)
    assert repo.cross_called is False


@pytest.mark.asyncio
async def test_cross_tenant_ok_for_super_admin():
    repo = _StubRepo()
    req = _make_request("super_admin", repo)  # level 100
    out = await metrics_usage_cross_tenant(req, limit=10)
    assert out["ok"] is True
    assert repo.cross_called is True
    assert out["meta"]["limit"] == 10
