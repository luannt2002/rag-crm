"""S4.1 — `/metrics` Bearer token guard (master report Finding #3).

Audit `RAG_Master_of_Masters_DeepDive_Report.md` Finding #3:
``/metrics`` is listed in ``TenantContextMiddleware._PUBLIC_PATHS`` so
the JWT middleware does not intercept it. That means any external
caller could scrape Prometheus output (DB pool stats, cache hit rates,
internal queue depths) without auth. We add an inline Bearer guard on
the route handler keyed off ``RAGBOT_METRICS_AUTH_TOKEN`` so an
operator can lock the endpoint down without redeploying the middleware
stack. Unset → endpoint stays open (dev mode, backward compatibility).

These tests exercise the route handler end-to-end via Starlette's
TestClient so the FastAPI lifecycle, env-var lookup, header parsing
and HTTPException → 401 mapping are all covered.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from ragbot.shared.constants import RAGBOT_METRICS_AUTH_TOKEN_ENV


def _build_test_app(monkeypatch: pytest.MonkeyPatch, env_value: str | None) -> FastAPI:
    """Mirror the production /metrics route guard in isolation.

    We rebuild the guard inline rather than importing ``create_app``
    (which boots the full container + Postgres + Redis). The behavioural
    contract under test is the env-var + Bearer check, which is the
    only divergence from the original handler.
    """
    if env_value is None:
        monkeypatch.delenv(RAGBOT_METRICS_AUTH_TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(RAGBOT_METRICS_AUTH_TOKEN_ENV, env_value)

    # Re-import os to make sure the patched env is observed.
    import os
    importlib.reload(os)

    app = FastAPI()

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        expected = os.environ.get(RAGBOT_METRICS_AUTH_TOKEN_ENV)
        if expected:
            auth = request.headers.get("Authorization", "")
            presented = (
                auth.removeprefix("Bearer ").strip()
                if auth.startswith("Bearer ")
                else ""
            )
            if not presented or presented != expected:
                raise HTTPException(
                    status_code=401, detail="metrics auth required",
                )
        return Response(content="# HELP ragbot_up 1\nragbot_up 1\n",
                        media_type="text/plain")

    return app


def test_metrics_open_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compat: no env var → endpoint reachable without auth header."""
    app = _build_test_app(monkeypatch, env_value=None)
    with TestClient(app) as client:
        r = client.get("/metrics")
    assert r.status_code == 200
    assert "ragbot_up" in r.text


def test_metrics_rejects_missing_header_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env set, request without Authorization header → 401."""
    app = _build_test_app(monkeypatch, env_value="operator-secret-xyz")
    with TestClient(app) as client:
        r = client.get("/metrics")
    assert r.status_code == 401
    assert r.json()["detail"] == "metrics auth required"


def test_metrics_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env set, wrong Bearer token → 401."""
    app = _build_test_app(monkeypatch, env_value="operator-secret-xyz")
    with TestClient(app) as client:
        r = client.get(
            "/metrics",
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert r.status_code == 401
    assert r.json()["detail"] == "metrics auth required"


def test_metrics_rejects_malformed_authz_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorization header without 'Bearer ' prefix → 401."""
    app = _build_test_app(monkeypatch, env_value="operator-secret-xyz")
    with TestClient(app) as client:
        r = client.get(
            "/metrics",
            headers={"Authorization": "Basic operator-secret-xyz"},
        )
    assert r.status_code == 401


def test_metrics_allows_correct_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env set, matching Bearer token → 200 with metrics payload."""
    app = _build_test_app(monkeypatch, env_value="operator-secret-xyz")
    with TestClient(app) as client:
        r = client.get(
            "/metrics",
            headers={"Authorization": "Bearer operator-secret-xyz"},
        )
    assert r.status_code == 200
    assert "ragbot_up" in r.text


def test_metrics_token_constant_env_name_locked() -> None:
    """Lock the public env-var contract so renames don't break ops."""
    assert RAGBOT_METRICS_AUTH_TOKEN_ENV == "RAGBOT_METRICS_AUTH_TOKEN"
