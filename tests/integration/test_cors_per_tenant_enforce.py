"""Per-tenant CORS enforcement e2e tests.

End-to-end verification that ``CORSPerTenantMiddleware`` (production ASGI
middleware at ``ragbot.interfaces.http.middlewares.cors_per_tenant``)
correctly consults ``tenants.allowed_origins`` (JSONB column) via
``TenantConfigCache`` at request time and enforces the per-tenant whitelist
on real HTTP traffic driven by FastAPI TestClient.

Drives the full middleware stack through TestClient with a tenant-context
stub injecting ``request.state.record_tenant_id`` (so the JWT layer is
bypassed without weakening the CORS surface under test).

Coverage matrix
---------------
1. ``test_origin_in_whitelist_allowed`` — tenant A's whitelisted Origin
   on a tenant-scoped POST → 200 + ``Access-Control-Allow-Origin``
   echoes the request Origin + ``Vary: Origin`` set.
2. ``test_origin_cross_tenant_rejected`` — preflight from tenant A
   carrying tenant B's whitelisted Origin (impersonation) → 403, no
   ``Access-Control-Allow-Origin`` header in response.
3. ``test_origin_other_tenant_whitelist_pass`` — tenant B's own
   whitelisted Origin under tenant B context → 200 + ACAO echo.
4. ``test_backcompat_tenant_no_origins_fail_closed`` — tenant with empty
   ``allowed_origins`` (owner has not seeded yet) — preflight from any
   browser Origin → 403 (deny-by-default contract).

Test doubles
------------
* ``_FakeTenantConfigCache`` — in-memory ``TenantConfigCache`` shaped
  to satisfy ``CORSPerTenantMiddleware._get_cache(...).get(...)``.
* ``_TenantContextStubMiddleware`` — picks ``X-Test-Tenant`` request
  header (test-only) → lifts onto ``request.state.record_tenant_id``,
  mirroring what the production ``TenantContextMiddleware`` does post
  JWT verify. Mounted OUTSIDE ``CORSPerTenantMiddleware`` so the inner
  CORS layer reads the lifted UUID just like production.

Domain-neutral
--------------
Test fixture origins use the IETF-reserved ``example.com``/``example``
hostnames (RFC 2606). No tenant / customer / brand literal in tests OR
production code under test.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from types import SimpleNamespace

from ragbot.application.services.tenant_config_cache import TenantRuntimeConfig
from ragbot.interfaces.http.middlewares.cors_per_tenant import (
    CORSPerTenantMiddleware,
)


# ---------------------------------------------------------------------------
# Fixture origins — RFC 2606 reserved (no real tenant brand).
# ---------------------------------------------------------------------------

_ORIGIN_A = "https://a.example.com"
_ORIGIN_B = "https://b.example.com"
_ORIGIN_C = "https://c.example.com"  # never whitelisted in any tenant


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeTenantConfigCache:
    """In-memory ``TenantConfigCache`` substitute.

    Behaves like the production cache for the slice the middleware
    consumes: ``await get(record_tenant_id) -> TenantRuntimeConfig | None``.
    """

    def __init__(self, mapping: dict[UUID, tuple[str, ...]]) -> None:
        self._map = mapping

    async def get(self, record_tenant_id: UUID) -> TenantRuntimeConfig | None:
        if record_tenant_id not in self._map:
            return None
        return TenantRuntimeConfig(
            bypass_rate_limit=False,
            rate_limit_per_min=None,
            monthly_token_cap=None,
            allowed_origins=self._map[record_tenant_id],
        )


class _TenantContextStubMiddleware(BaseHTTPMiddleware):
    """Lifts a test header into ``request.state.record_tenant_id``.

    Mirrors the production ``TenantContextMiddleware`` post-auth bind so
    ``CORSPerTenantMiddleware`` reads the same shape it does in prod
    without dragging the full JWT path into this test module. The header
    is ``X-Test-Tenant`` — a value not present on real wire traffic.
    Missing / unparseable header → ``record_tenant_id = None`` (caller
    treats as anonymous, falls through to the global env list).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        raw = request.headers.get("X-Test-Tenant", "")
        record_tenant_id: UUID | None
        try:
            record_tenant_id = UUID(raw) if raw else None
        except (TypeError, ValueError):
            record_tenant_id = None
        request.state.record_tenant_id = record_tenant_id
        return await call_next(request)


def _build_app(
    *,
    cache: _FakeTenantConfigCache,
    global_origins: tuple[str, ...] = (),
) -> FastAPI:
    """Compose a minimal FastAPI app with the production CORS middleware.

    Wiring order matches ``ragbot.interfaces.http.app.create_app``:
    ``TenantContext`` (outer, runs first on request) lifts
    ``record_tenant_id``; ``CORSPerTenant`` (inner) reads it. Routes
    return a tiny JSON response so we can verify response headers
    end-to-end. ``app.state.container`` exposes the fake cache via the
    ``tenant_config_cache()`` accessor — the same shape the production
    DI container provides.
    """
    app = FastAPI()
    app.state.container = SimpleNamespace(
        tenant_config_cache=lambda: cache,
    )

    # Inner: production CORS middleware under test.
    app.add_middleware(
        CORSPerTenantMiddleware,
        global_origins=global_origins,
    )
    # Outer: tenant-context stub. add_middleware stacks LIFO so this is
    # added LAST and therefore runs FIRST on request — exactly the prod
    # order (TenantContext outside, CORSPerTenant inside).
    app.add_middleware(_TenantContextStubMiddleware)

    @app.post("/api/ragbot/test/chat")
    async def chat() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/ragbot/test/ping")
    async def ping() -> dict[str, Any]:
        return {"pong": True}

    return app


def _client(
    cache: _FakeTenantConfigCache,
    *,
    global_origins: tuple[str, ...] = (),
) -> TestClient:
    return TestClient(
        _build_app(cache=cache, global_origins=global_origins),
        raise_server_exceptions=False,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — origin in tenant whitelist allowed
# ---------------------------------------------------------------------------


class TestOriginInWhitelistAllowed:
    """Tenant A whitelist contains _ORIGIN_A; request from _ORIGIN_A passes."""

    def test_post_chat_origin_in_whitelist_returns_200_with_acao(self) -> None:
        tid_a = uuid4()
        cache = _FakeTenantConfigCache({tid_a: (_ORIGIN_A,)})
        client = _client(cache)

        resp = client.post(
            "/api/ragbot/test/chat",
            json={"q": "hello"},
            headers={
                "Origin": _ORIGIN_A,
                "X-Test-Tenant": str(tid_a),
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert resp.headers["access-control-allow-origin"] == _ORIGIN_A
        assert resp.headers["access-control-allow-credentials"] == "true"
        # Vary: Origin must be present so caches do not leak ACAO across
        # tenants on the same path/method bucket.
        assert "origin" in resp.headers.get("vary", "").lower()

    def test_preflight_origin_in_whitelist_returns_204(self) -> None:
        tid_a = uuid4()
        cache = _FakeTenantConfigCache({tid_a: (_ORIGIN_A,)})
        client = _client(cache)

        resp = client.options(
            "/api/ragbot/test/chat",
            headers={
                "Origin": _ORIGIN_A,
                "Access-Control-Request-Method": "POST",
                "X-Test-Tenant": str(tid_a),
            },
        )

        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == _ORIGIN_A
        assert "POST" in resp.headers["access-control-allow-methods"]
        assert "Authorization" in resp.headers["access-control-allow-headers"]
        assert int(resp.headers["access-control-max-age"]) > 0


# ---------------------------------------------------------------------------
# Scenario 2 — cross-tenant origin spoof rejected
# ---------------------------------------------------------------------------


class TestOriginCrossTenantRejected:
    """Tenant A whitelist contains _ORIGIN_A only; tenant A context with
    Origin=_ORIGIN_B (a different tenant's whitelisted origin) → 403 on
    preflight, and no ACAO emitted on the actual request."""

    def test_preflight_cross_tenant_origin_returns_403(self) -> None:
        tid_a = uuid4()
        tid_b = uuid4()
        cache = _FakeTenantConfigCache({
            tid_a: (_ORIGIN_A,),
            tid_b: (_ORIGIN_B,),
        })
        client = _client(cache)

        resp = client.options(
            "/api/ragbot/test/chat",
            headers={
                # Caller logs in as tenant A but tries to use B's origin.
                "Origin": _ORIGIN_B,
                "Access-Control-Request-Method": "POST",
                "X-Test-Tenant": str(tid_a),
            },
        )

        assert resp.status_code == 403
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "cors_origin_rejected"
        # No ACAO header on the rejected preflight (browser must block).
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers
        }

    def test_actual_request_cross_tenant_origin_omits_acao(self) -> None:
        tid_a = uuid4()
        tid_b = uuid4()
        cache = _FakeTenantConfigCache({
            tid_a: (_ORIGIN_A,),
            tid_b: (_ORIGIN_B,),
        })
        client = _client(cache)

        resp = client.post(
            "/api/ragbot/test/chat",
            json={"q": "spoof"},
            headers={
                "Origin": _ORIGIN_B,
                "X-Test-Tenant": str(tid_a),
            },
        )

        # Per CORS spec: server returns 200 to its own backend; the
        # absent ACAO causes the browser-side fetch().then to fail,
        # protecting the cross-tenant boundary. The middleware does NOT
        # 403 the actual request — only preflights — so the surface
        # under test is "ACAO header absent".
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers
        }


# ---------------------------------------------------------------------------
# Scenario 3 — different tenant's own whitelist passes
# ---------------------------------------------------------------------------


class TestOriginOtherTenantWhitelistPass:
    """Tenant B with whitelist containing _ORIGIN_B; request under tenant
    B context with Origin=_ORIGIN_B → 200 + ACAO echo. Proves whitelist
    isolation works the other direction (tenant B is not coupled to A)."""

    def test_tenant_b_own_origin_allowed(self) -> None:
        tid_a = uuid4()
        tid_b = uuid4()
        cache = _FakeTenantConfigCache({
            tid_a: (_ORIGIN_A,),
            tid_b: (_ORIGIN_B,),
        })
        client = _client(cache)

        resp = client.post(
            "/api/ragbot/test/chat",
            json={"q": "hi"},
            headers={
                "Origin": _ORIGIN_B,
                "X-Test-Tenant": str(tid_b),
            },
        )

        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == _ORIGIN_B
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_tenant_b_preflight_passes(self) -> None:
        tid_b = uuid4()
        cache = _FakeTenantConfigCache({tid_b: (_ORIGIN_B,)})
        client = _client(cache)

        resp = client.options(
            "/api/ragbot/test/chat",
            headers={
                "Origin": _ORIGIN_B,
                "Access-Control-Request-Method": "POST",
                "X-Test-Tenant": str(tid_b),
            },
        )

        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == _ORIGIN_B


# ---------------------------------------------------------------------------
# Scenario 4 — tenant with empty allowed_origins fails closed
# ---------------------------------------------------------------------------


class TestLegacyTenantNoOriginsFailClosed:
    """Tenant exists but has empty ``allowed_origins`` (owner has not
    seeded yet). Any browser cross-origin request must be rejected —
    the deny-by-default contract guards against operators forgetting to
    set the column. Bot owner must set origins via
    PATCH /admin/tenants/{id} before browser traffic flows."""

    def test_preflight_with_empty_origins_returns_403(self) -> None:
        tid_c = uuid4()
        cache = _FakeTenantConfigCache({tid_c: ()})
        client = _client(cache)

        resp = client.options(
            "/api/ragbot/test/chat",
            headers={
                "Origin": _ORIGIN_C,
                "Access-Control-Request-Method": "POST",
                "X-Test-Tenant": str(tid_c),
            },
        )

        assert resp.status_code == 403
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "cors_origin_rejected"
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers
        }

    def test_actual_request_with_empty_origins_omits_acao(self) -> None:
        tid_c = uuid4()
        cache = _FakeTenantConfigCache({tid_c: ()})
        client = _client(cache)

        resp = client.post(
            "/api/ragbot/test/chat",
            json={"q": "anything"},
            headers={
                "Origin": _ORIGIN_C,
                "X-Test-Tenant": str(tid_c),
            },
        )

        # Same shape as Scenario 2: server returns 200, ACAO omitted →
        # browser blocks the read. Server-side data path stays
        # untouched (this middleware is transport-only per the
        # Application MINDSET — no answer / template injection).
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers
        }

    def test_unknown_tenant_uuid_treated_as_no_origins(self) -> None:
        """Tenant UUID that the cache has never heard of (cache.get →
        None) must also fail-closed. Defence in depth vs. a JWT issued
        for a tenant row deleted out from under us."""
        unknown_tid = uuid4()
        # Cache contains nothing for this UUID.
        cache = _FakeTenantConfigCache({})
        client = _client(cache)

        resp = client.options(
            "/api/ragbot/test/chat",
            headers={
                "Origin": _ORIGIN_C,
                "Access-Control-Request-Method": "POST",
                "X-Test-Tenant": str(unknown_tid),
            },
        )

        assert resp.status_code == 403
