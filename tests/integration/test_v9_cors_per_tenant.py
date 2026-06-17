"""Per-tenant CORS strict whitelist tests.

Coverage matrix
---------------
1. ``origin_matches`` exact + wildcard + multi-pattern + edge cases.
2. Preflight OPTIONS from a tenant-allowed origin → 204 with
   ``Access-Control-Allow-*`` headers.
3. Preflight from a not-whitelisted origin → 403, no ACAO.
4. Wildcard ``https://*.example.com`` matches subdomain only (not the
   apex, not a different domain).
5. Empty ``allowed_origins`` (deny-default) rejects every preflight.
6. Pre-auth path (``/health``) uses the global env-driven list.
7. Non-preflight: response carries ACAO + ACAC + Vary when whitelisted.
8. Non-preflight: response omits ACAO when origin not whitelisted.
9. ``parse_global_origins`` JSON parser tolerates malformed input
   (defence in depth — bad APP_CORS_ALLOWED_ORIGINS env is non-fatal).

Test doubles
------------
* ``_FakeCache`` — in-memory ``TenantConfigCache`` stand-in returning
  pre-seeded ``TenantRuntimeConfig`` per tenant UUID.
* Hand-built ``Request`` / ``call_next`` so we exercise the middleware
  in isolation without needing the full FastAPI lifespan.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from starlette.responses import PlainTextResponse, Response

from ragbot.application.services.tenant_config_cache import TenantRuntimeConfig
from ragbot.interfaces.http.middlewares.cors_per_tenant import (
    CORSPerTenantMiddleware,
    origin_matches,
    parse_global_origins,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCache:
    def __init__(self, mapping: dict[UUID, tuple[str, ...]]) -> None:
        self._map = mapping
        self.lookups: list[UUID] = []

    async def get(self, record_tenant_id: UUID) -> TenantRuntimeConfig | None:
        self.lookups.append(record_tenant_id)
        origins = self._map.get(record_tenant_id)
        if origins is None:
            return None
        return TenantRuntimeConfig(
            bypass_rate_limit=False,
            rate_limit_per_min=None,
            monthly_token_cap=None,
            allowed_origins=origins,
        )


def _make_request(
    *,
    method: str,
    path: str,
    origin: str | None,
    record_tenant_id: UUID | None,
    cache: _FakeCache,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers_dict: dict[str, str] = {}
    if origin is not None:
        headers_dict["origin"] = origin
    if extra_headers:
        headers_dict.update(extra_headers)

    class _Headers(dict):
        def get(self, k: str, default: str = "") -> str:  # type: ignore[override]
            return super().get(k.lower(), default)

        def __contains__(self, k: object) -> bool:  # type: ignore[override]
            return super().__contains__(str(k).lower())

    h = _Headers({k.lower(): v for k, v in headers_dict.items()})

    container = SimpleNamespace(tenant_config_cache=lambda: cache)
    app_state = SimpleNamespace(container=container)
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=h,
        state=SimpleNamespace(record_tenant_id=record_tenant_id),
        app=SimpleNamespace(state=app_state),
    )


async def _ok_call_next(_request: Any) -> Response:
    return PlainTextResponse("ok")


# ---------------------------------------------------------------------------
# 1. origin_matches helper
# ---------------------------------------------------------------------------


class TestOriginMatches:
    def test_exact_match(self) -> None:
        assert origin_matches(
            "https://app.example.com", ("https://app.example.com",),
        )

    def test_exact_mismatch(self) -> None:
        assert not origin_matches(
            "https://other.example.com", ("https://app.example.com",),
        )

    def test_wildcard_subdomain(self) -> None:
        assert origin_matches(
            "https://api.example.com", ("https://*.example.com",),
        )
        assert origin_matches(
            "https://staging.api.example.com", ("https://*.example.com",),
        )

    def test_wildcard_does_not_match_apex(self) -> None:
        # Wildcard *.example.com requires a non-empty subdomain segment.
        assert not origin_matches(
            "https://example.com", ("https://*.example.com",),
        )

    def test_wildcard_does_not_match_different_domain(self) -> None:
        assert not origin_matches(
            "https://app.other.com", ("https://*.example.com",),
        )

    def test_wildcard_scheme_strict(self) -> None:
        # http: cannot match https:* pattern.
        assert not origin_matches(
            "http://api.example.com", ("https://*.example.com",),
        )

    def test_empty_allowed_blocks(self) -> None:
        assert not origin_matches("https://app.example.com", ())

    def test_empty_origin_blocks(self) -> None:
        assert not origin_matches("", ("https://app.example.com",))

    def test_star_pattern_allows_all(self) -> None:
        # Operator opt-in dev-only escape hatch.
        assert origin_matches("https://anything", ("*",))


# ---------------------------------------------------------------------------
# 2. Preflight OPTIONS
# ---------------------------------------------------------------------------


class TestPreflight:
    @pytest.mark.asyncio
    async def test_allowed_origin_returns_204_with_acao(self) -> None:
        tid = uuid4()
        cache = _FakeCache({tid: ("https://app.t1.example",)})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="OPTIONS",
            path="/api/ragbot/test/chat",
            origin="https://app.t1.example",
            record_tenant_id=tid,
            cache=cache,
            extra_headers={"access-control-request-method": "POST"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 204
        assert resp.headers["Access-Control-Allow-Origin"] == "https://app.t1.example"
        assert "POST" in resp.headers["Access-Control-Allow-Methods"]
        assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"
        assert int(resp.headers["Access-Control-Max-Age"]) > 0

    @pytest.mark.asyncio
    async def test_disallowed_origin_returns_403(self) -> None:
        tid = uuid4()
        cache = _FakeCache({tid: ("https://app.t1.example",)})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="OPTIONS",
            path="/api/ragbot/test/chat",
            origin="https://evil.example",
            record_tenant_id=tid,
            cache=cache,
            extra_headers={"access-control-request-method": "POST"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 403
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers
        }

    @pytest.mark.asyncio
    async def test_wildcard_subdomain_preflight(self) -> None:
        tid = uuid4()
        cache = _FakeCache({tid: ("https://*.t2.example",)})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="OPTIONS",
            path="/api/ragbot/test/chat",
            origin="https://sub.t2.example",
            record_tenant_id=tid,
            cache=cache,
            extra_headers={"access-control-request-method": "POST"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_empty_allowed_blocks_all_preflight(self) -> None:
        tid = uuid4()
        cache = _FakeCache({tid: ()})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="OPTIONS",
            path="/api/ragbot/test/chat",
            origin="https://anyone.example",
            record_tenant_id=tid,
            cache=cache,
            extra_headers={"access-control-request-method": "POST"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. Pre-auth fallback to global env list
# ---------------------------------------------------------------------------


class TestPreAuthFallback:
    @pytest.mark.asyncio
    async def test_health_uses_global_origins(self) -> None:
        cache = _FakeCache({})
        mw = CORSPerTenantMiddleware(
            app=lambda *_: None,
            global_origins=("https://monitor.example",),
        )
        req = _make_request(
            method="OPTIONS",
            path="/health",
            origin="https://monitor.example",
            record_tenant_id=None,
            cache=cache,
            extra_headers={"access-control-request-method": "GET"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 204
        assert resp.headers["Access-Control-Allow-Origin"] == "https://monitor.example"
        # Pre-auth path → cache must NOT be consulted.
        assert cache.lookups == []

    @pytest.mark.asyncio
    async def test_health_origin_not_in_global_returns_403(self) -> None:
        cache = _FakeCache({})
        mw = CORSPerTenantMiddleware(
            app=lambda *_: None,
            global_origins=("https://monitor.example",),
        )
        req = _make_request(
            method="OPTIONS",
            path="/health",
            origin="https://evil.example",
            record_tenant_id=None,
            cache=cache,
            extra_headers={"access-control-request-method": "GET"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Non-preflight path attaches ACAO on response
# ---------------------------------------------------------------------------


class TestNonPreflight:
    @pytest.mark.asyncio
    async def test_response_carries_acao_for_whitelisted(self) -> None:
        tid = uuid4()
        cache = _FakeCache({tid: ("https://app.t1.example",)})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="POST",
            path="/api/ragbot/test/chat",
            origin="https://app.t1.example",
            record_tenant_id=tid,
            cache=cache,
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "https://app.t1.example"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"
        assert "Origin" in resp.headers.get("Vary", "")

    @pytest.mark.asyncio
    async def test_response_omits_acao_when_not_whitelisted(self) -> None:
        tid = uuid4()
        cache = _FakeCache({tid: ("https://app.t1.example",)})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="POST",
            path="/api/ragbot/test/chat",
            origin="https://evil.example",
            record_tenant_id=tid,
            cache=cache,
        )
        resp = await mw.dispatch(req, _ok_call_next)
        # Browser-side: no ACAO header => the JS read fails. Status 200
        # is returned to the *server* — middleware does not 403 actual
        # requests, only preflights, per spec.
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers
        }

    @pytest.mark.asyncio
    async def test_no_origin_passes_through(self) -> None:
        # Non-browser caller (server-to-server) — never blocked.
        tid = uuid4()
        cache = _FakeCache({tid: ()})
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        req = _make_request(
            method="POST",
            path="/api/ragbot/test/chat",
            origin=None,
            record_tenant_id=tid,
            cache=cache,
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. Cross-tenant isolation
# ---------------------------------------------------------------------------


class TestCrossTenantIsolation:
    @pytest.mark.asyncio
    async def test_tenant_a_origin_blocked_for_tenant_b(self) -> None:
        tid_a = uuid4()
        tid_b = uuid4()
        cache = _FakeCache({
            tid_a: ("https://app.a.example",),
            tid_b: ("https://app.b.example",),
        })
        mw = CORSPerTenantMiddleware(app=lambda *_: None, global_origins=())
        # Tenant B sends an origin that is whitelisted for tenant A.
        req = _make_request(
            method="OPTIONS",
            path="/api/ragbot/test/chat",
            origin="https://app.a.example",
            record_tenant_id=tid_b,
            cache=cache,
            extra_headers={"access-control-request-method": "POST"},
        )
        resp = await mw.dispatch(req, _ok_call_next)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 6. parse_global_origins helper — defence vs malformed env
# ---------------------------------------------------------------------------


class TestParseGlobalOrigins:
    def test_parses_valid_json_array(self) -> None:
        raw = json.dumps(["https://a.example", "https://b.example"])
        assert parse_global_origins(raw) == (
            "https://a.example", "https://b.example",
        )

    def test_empty_string_returns_empty_tuple(self) -> None:
        assert parse_global_origins("") == ()
        assert parse_global_origins(None) == ()

    def test_malformed_json_returns_empty_tuple(self) -> None:
        assert parse_global_origins("not-json") == ()

    def test_non_array_returns_empty_tuple(self) -> None:
        assert parse_global_origins('{"x":1}') == ()

    def test_filters_non_string_entries(self) -> None:
        raw = json.dumps(["https://a.example", 42, None, "https://b.example"])
        assert parse_global_origins(raw) == (
            "https://a.example", "https://b.example",
        )
