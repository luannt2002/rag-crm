"""Integration tests -- verify HTTP routes via FastAPI TestClient.

These tests use the real app but mock external services (DB, Redis, LLM).
They verify: routing, middleware, request/response schemas, error codes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@asynccontextmanager
async def _noop_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Skip all real bootstrap (DB, Redis, LLM)."""
    mock_container = MagicMock()
    mock_settings = MagicMock()
    mock_settings.app.env = "development"
    mock_settings.app.api_token = ""
    mock_settings.app.version = "0.0.0-test"
    mock_settings.observability.prometheus_path = "/metrics"
    mock_settings.observability.log_level = "WARNING"
    mock_settings.observability.log_format = "text"
    application.state.container = mock_container
    application.state.settings = mock_settings
    application.state.dev_jwt_secret = "test-secret-for-integration"
    yield


@pytest.fixture()
def client():
    """Create TestClient with mocked lifespan to avoid real DB/Redis."""
    import importlib
    import sys

    # importlib.import_module returns the real module, not the re-exported
    # FastAPI app object that `import ragbot.interfaces.http.app` resolves to.
    app_mod = importlib.import_module("ragbot.interfaces.http.app")
    original_lifespan = app_mod.lifespan  # type: ignore[attr-defined]
    app_mod.lifespan = _noop_lifespan  # type: ignore[attr-defined]
    try:
        application = app_mod.create_app()  # type: ignore[attr-defined]
        with TestClient(application, raise_server_exceptions=False) as c:
            yield c
    finally:
        app_mod.lifespan = original_lifespan  # type: ignore[attr-defined]


class TestHealthEndpoints:
    def test_health_returns_200(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_reports_dependencies_and_pool_stats(self, client: TestClient) -> None:
        resp = client.get("/health")
        body = resp.json()
        assert body["status"] in ("ok", "degraded", "down")
        assert "postgres" in body["dependencies"]
        assert "redis" in body["dependencies"]
        # pool_stats may be empty in test harness (async engine not started),
        # but the key must exist per the schema contract.
        assert "pool_stats" in body


class TestAuthMiddleware:
    def test_protected_route_without_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/ragbot/bots")
        assert resp.status_code in (401, 403, 404)

    def test_demo_ragbot_bypasses_auth(self, client: TestClient) -> None:
        resp = client.get("/demo-ragbot/test")
        assert resp.status_code != 401

    def test_health_bypasses_auth(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_static_bypasses_auth(self, client: TestClient) -> None:
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code != 401

    def test_metrics_bypasses_auth(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code != 401


class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_format(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/plain" in ct or "openmetrics" in ct

    def test_metrics_body_not_empty(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert len(resp.content) > 0


class TestRequestValidation:
    def test_chat_post_without_body_returns_error(self, client: TestClient) -> None:
        """POST without JSON body should fail validation, not 500."""
        resp = client.post(
            "/api/ragbot/chat",
            headers={"Authorization": "Bearer fake-token"},
        )
        # 401 (no valid token) or 422 (validation) -- both acceptable
        assert resp.status_code in (401, 403, 422)

    def test_unknown_route_returns_404_or_401(self, client: TestClient) -> None:
        resp = client.get("/api/ragbot/this-does-not-exist-at-all")
        assert resp.status_code in (401, 404)


class TestMethodNotAllowed:
    def test_health_post_returns_405(self, client: TestClient) -> None:
        resp = client.post("/health")
        assert resp.status_code == 405
