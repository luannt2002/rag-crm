"""Unit tests: P4 audit analytics — endpoint contract + RBAC enforcement."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from ragbot.interfaces.http.routes.admin_audit import router
from ragbot.shared.errors import ForbiddenError

_TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _make_app(role: str = "admin") -> FastAPI:
    """Create minimal FastAPI app with audit router and mock container."""
    app = FastAPI()
    app.include_router(router, prefix="/api/ragbot/admin")

    mock_container = MagicMock()

    # Mock audit_repo
    mock_audit = AsyncMock()
    mock_audit.get_audit_overview.return_value = {
        "documents": {"total": 10, "total_chars": 50000, "avg_chars": 5000},
        "chunks": {"total": 100, "avg_chars": 500, "avg_per_doc": 10.0, "strategy_distribution": {"hdt": 60}},
        "queries": {"total": 50, "avg_latency_ms": 1500.0, "p50_latency_ms": 1200.0,
                     "p95_latency_ms": 3000.0, "p99_latency_ms": 5000.0, "cache_hit_rate": 0.2},
        "tokens": {"total_prompt": 100000, "total_completion": 30000, "total_cost_usd": 5.0,
                    "avg_per_request": {"prompt": 2000, "completion": 600, "cost_usd": 0.1}},
    }
    mock_audit.get_query_detail.return_value = {
        "queries": [{"request_id": "abc", "duration_ms": 1200, "status": "success"}],
        "pagination": {"limit": 20, "count": 1, "has_more": False, "next_cursor": None},
    }
    mock_container.audit_repo.return_value = mock_audit

    # Mock invocation_logger
    mock_logger = AsyncMock()
    mock_logger.fetch_by_message_id.return_value = {"request_logs": [], "steps": []}
    mock_container.invocation_logger.return_value = mock_logger

    app.state.container = mock_container

    @app.exception_handler(ForbiddenError)
    async def _forbidden_handler(request, exc):  # noqa: ARG001
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.middleware("http")
    async def inject_role(request, call_next):
        request.state.role = role
        # Audit routes now require record_tenant_id (UUID) on request.state.
        # Real middleware populates this from JWT; the test injects a stable
        # UUID so route handlers can scope tenant queries.
        request.state.record_tenant_id = _TEST_TENANT_ID
        return await call_next(request)

    return app


class TestAuditOverview:
    def test_overview_returns_stats(self) -> None:
        app = _make_app(role="admin")
        client = TestClient(app)
        resp = client.get("/api/ragbot/admin/audit/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["documents"]["total"] == 10
        assert data["data"]["queries"]["total"] == 50
        assert data["data"]["tokens"]["total_cost_usd"] == 5.0

    def test_overview_requires_admin_level(self) -> None:
        app = _make_app(role="user")
        client = TestClient(app)
        resp = client.get("/api/ragbot/admin/audit/overview")
        assert resp.status_code == 403

    def test_overview_forwards_tenant_scope_to_repo(self) -> None:
        """P17 P0-2: endpoint must pass caller's record_tenant_id
        to the repo so a tenant admin cannot read other tenants' data.
        """
        app = _make_app(role="admin")
        # Reach into the mocked container to inspect call kwargs
        repo = app.state.container.audit_repo.return_value
        client = TestClient(app)
        resp = client.get("/api/ragbot/admin/audit/overview")
        assert resp.status_code == 200
        call_kwargs = repo.get_audit_overview.await_args.kwargs
        assert call_kwargs["record_tenant_id"] == _TEST_TENANT_ID

    def test_overview_accepts_date_filters(self) -> None:
        app = _make_app(role="admin")
        client = TestClient(app)
        resp = client.get(
            "/api/ragbot/admin/audit/overview",
            params={"date_from": "2026-04-01T00:00:00", "date_to": "2026-04-21T23:59:59"},
        )
        assert resp.status_code == 200


class TestAuditQueryDetail:
    def test_query_detail_returns_list(self) -> None:
        app = _make_app(role="admin")
        client = TestClient(app)
        resp = client.get("/api/ragbot/admin/audit/query-detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert isinstance(data["data"]["queries"], list)
        assert data["data"]["pagination"]["count"] == 1

    def test_query_detail_requires_admin(self) -> None:
        app = _make_app(role="viewer")
        client = TestClient(app)
        resp = client.get("/api/ragbot/admin/audit/query-detail")
        assert resp.status_code == 403

    def test_query_detail_accepts_pagination(self) -> None:
        app = _make_app(role="admin")
        client = TestClient(app)
        resp = client.get(
            "/api/ragbot/admin/audit/query-detail",
            params={"limit": 5},
        )
        assert resp.status_code == 200
