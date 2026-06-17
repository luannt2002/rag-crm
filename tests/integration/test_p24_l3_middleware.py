"""Wave 1 Lane L3 — HTTP middleware integration tests.

Covers:
- BodySizeLimitMiddleware: per-path limits, 413 response shape.
- CORSMiddleware: allowlist wiring via APP_CORS_ALLOWED_ORIGINS env var.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragbot.shared.constants import (
    DEFAULT_MAX_BODY_CHAT_BYTES,
    DEFAULT_MAX_BODY_INGEST_BYTES,
)


@asynccontextmanager
async def _noop_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Skip bootstrap (DB/Redis/LLM) — tests only exercise middleware."""
    mock_container = MagicMock()
    mock_settings = MagicMock()
    mock_settings.app.env = "development"
    mock_settings.app.api_token = ""
    mock_settings.app.version = "0.0.0-test"
    mock_settings.app.cors_allowed_origins = "[]"
    mock_settings.observability.prometheus_path = "/metrics"
    mock_settings.observability.log_level = "WARNING"
    mock_settings.observability.log_format = "text"
    application.state.container = mock_container
    application.state.settings = mock_settings
    application.state.dev_jwt_secret = "test-secret-for-l3"
    yield


def _build_client(cors_env: str | None = None) -> TestClient:
    """Create TestClient with mocked lifespan + controlled CORS env."""
    from ragbot.config import settings as settings_mod
    # Clear the lru_cache + mutate AppSettings default via env.
    settings_mod.get_settings.cache_clear()
    import os
    if cors_env is not None:
        os.environ["APP_CORS_ALLOWED_ORIGINS"] = cors_env
    else:
        os.environ.pop("APP_CORS_ALLOWED_ORIGINS", None)
    # Disable the IP rate-limit + anti-abuse layer for the L3
    # body-size + CORS tests; otherwise the IP RL fail-closed path
    # short-circuits with 503 before the body-size gate runs (the test
    # _noop_lifespan installs a MagicMock container that doesn't speak
    # Redis). The L3 surface under test is BodySize + CORS only.
    os.environ["APP_IP_RATE_LIMIT_ENABLED"] = "false"
    os.environ["APP_ANTI_ABUSE_ENABLED"] = "false"

    app_mod = importlib.import_module("ragbot.interfaces.http.app")
    original_lifespan = app_mod.lifespan  # type: ignore[attr-defined]
    app_mod.lifespan = _noop_lifespan  # type: ignore[attr-defined]
    try:
        application = app_mod.create_app()  # type: ignore[attr-defined]
        return TestClient(application, raise_server_exceptions=False)
    finally:
        app_mod.lifespan = original_lifespan  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Body-size limit
# ---------------------------------------------------------------------------


class TestBodySizeLimit:
    def test_body_size_limit_chat_rejects_large(self) -> None:
        """Chat path (256 KB limit): 500 KB content-length → 413."""
        client = _build_client(cors_env="[]")
        # Fabricate an oversize Content-Length header without actually sending
        # that many bytes — middleware reads the header, not the body.
        oversize = DEFAULT_MAX_BODY_CHAT_BYTES + 1
        fake_body = b"x" * 10  # actual body is tiny; header is the trigger
        resp = client.post(
            "/api/ragbot/test/chat",
            content=fake_body,
            headers={"Content-Length": str(oversize)},
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert body["error"]["details"]["limit"] == DEFAULT_MAX_BODY_CHAT_BYTES

    def test_body_size_limit_chat_accepts_small(self) -> None:
        """Chat path (256 KB limit): 1 KB content-length → NOT 413."""
        client = _build_client(cors_env="[]")
        small_body = b"{}"
        resp = client.post(
            "/api/ragbot/test/chat",
            content=small_body,
            headers={"Content-Length": "1024"},
        )
        assert resp.status_code != 413

    def test_body_size_limit_documents_allows_larger(self) -> None:
        """Documents path (16 MB limit): 500 KB → NOT 413."""
        client = _build_client(cors_env="[]")
        resp = client.post(
            "/api/ragbot/documents",
            content=b"{}",
            headers={"Content-Length": "500000"},
        )
        assert resp.status_code != 413

    def test_body_size_limit_documents_rejects_over_cap(self) -> None:
        """Documents path: content-length over 16 MB cap → 413."""
        client = _build_client(cors_env="[]")
        oversize = DEFAULT_MAX_BODY_INGEST_BYTES + 1
        resp = client.post(
            "/api/ragbot/documents",
            content=b"{}",
            headers={"Content-Length": str(oversize)},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCors:
    def test_cors_disabled_when_no_origins(self) -> None:
        """Empty allowlist → no Access-Control-Allow-Origin on response."""
        client = _build_client(cors_env="[]")
        resp = client.get("/health", headers={"Origin": "http://example.com"})
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers.keys()
        }

    def test_cors_enabled_with_allowlist(self) -> None:
        """Configured origin → OPTIONS preflight returns CORS headers."""
        client = _build_client(cors_env='["http://example.com"]')
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        # Preflight should succeed (200 or 204) and echo back the origin.
        assert resp.status_code in (200, 204)
        allow_origin = resp.headers.get("access-control-allow-origin")
        assert allow_origin == "http://example.com"
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "GET" in allow_methods

    def test_cors_origin_not_in_allowlist_no_headers(self) -> None:
        """Origin outside allowlist → no matching CORS header echoed."""
        client = _build_client(cors_env='["http://only-this.example"]')
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://someone-else.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin != "http://someone-else.example"
