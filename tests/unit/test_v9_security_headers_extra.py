"""Extended SecurityHeadersMiddleware coverage.

Pins the new headers added on top of the Y4 baseline:
* Cross-Origin-Opener-Policy
* Cross-Origin-Resource-Policy
* X-Permitted-Cross-Domain-Policies
* Cross-Origin-Embedder-Policy (only on /docs, /redoc, /openapi.json)
* HSTS env-toggle still honoured (regression guard for Y4 contract)
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragbot.interfaces.http.middlewares.security_headers import (
    SecurityHeadersMiddleware,
)
from ragbot.shared.constants import (
    DEFAULT_SECURITY_HEADERS_COEP_DOCS_ONLY,
    DEFAULT_SECURITY_HEADERS_COOP,
    DEFAULT_SECURITY_HEADERS_CORP,
    DEFAULT_SECURITY_HEADERS_HSTS_VALUE,
    DEFAULT_SECURITY_HEADERS_PERMISSIONS_POLICY,
    DEFAULT_SECURITY_HEADERS_PERMITTED_CROSS_DOMAIN,
    DEFAULT_SECURITY_HEADERS_REFERRER_POLICY,
)


def _make_app(*, hsts_enabled: bool = False) -> TestClient:
    """Build a tiny app exercising only the SecurityHeadersMiddleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=hsts_enabled)

    @app.get("/anywhere")
    async def anywhere() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/docs")
    async def docs() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/openapi.json")
    async def openapi_json() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app)


class TestExtendedHeaders:
    def test_coop_emitted_default(self) -> None:
        client = _make_app()
        r = client.get("/anywhere")
        assert (
            r.headers.get("Cross-Origin-Opener-Policy")
            == DEFAULT_SECURITY_HEADERS_COOP
        )

    def test_corp_emitted_default(self) -> None:
        client = _make_app()
        r = client.get("/anywhere")
        assert (
            r.headers.get("Cross-Origin-Resource-Policy")
            == DEFAULT_SECURITY_HEADERS_CORP
        )

    def test_permitted_cross_domain_emitted_default(self) -> None:
        client = _make_app()
        r = client.get("/anywhere")
        assert (
            r.headers.get("X-Permitted-Cross-Domain-Policies")
            == DEFAULT_SECURITY_HEADERS_PERMITTED_CROSS_DOMAIN
        )

    def test_permissions_policy_emitted_default(self) -> None:
        """Y4 baseline still honoured."""
        client = _make_app()
        r = client.get("/anywhere")
        assert (
            r.headers.get("Permissions-Policy")
            == DEFAULT_SECURITY_HEADERS_PERMISSIONS_POLICY
        )

    def test_referrer_policy_emitted_default(self) -> None:
        client = _make_app()
        r = client.get("/anywhere")
        assert (
            r.headers.get("Referrer-Policy")
            == DEFAULT_SECURITY_HEADERS_REFERRER_POLICY
        )


class TestCoepPathScoping:
    def test_coep_emitted_on_docs(self) -> None:
        client = _make_app()
        r = client.get("/docs")
        assert (
            r.headers.get("Cross-Origin-Embedder-Policy")
            == DEFAULT_SECURITY_HEADERS_COEP_DOCS_ONLY
        )

    def test_coep_emitted_on_openapi(self) -> None:
        client = _make_app()
        r = client.get("/openapi.json")
        assert (
            r.headers.get("Cross-Origin-Embedder-Policy")
            == DEFAULT_SECURITY_HEADERS_COEP_DOCS_ONLY
        )

    def test_coep_NOT_emitted_on_arbitrary_path(self) -> None:
        """COEP must NOT land on arbitrary routes — would break browser POST CORS."""
        client = _make_app()
        r = client.get("/anywhere")
        assert "Cross-Origin-Embedder-Policy" not in r.headers


class TestHstsEnvToggle:
    def test_hsts_disabled_default_no_header(self) -> None:
        client = _make_app(hsts_enabled=False)
        r = client.get("/anywhere")
        # Y4 contract: HSTS only when explicitly enabled (TLS environments).
        assert "Strict-Transport-Security" not in r.headers

    def test_hsts_enabled_emits_long_max_age(self) -> None:
        client = _make_app(hsts_enabled=True)
        r = client.get("/anywhere")
        hsts = r.headers.get("Strict-Transport-Security")
        assert hsts == DEFAULT_SECURITY_HEADERS_HSTS_VALUE
        # Sanity: directive must include long max-age + subdomain coverage.
        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts


class TestRouteHandlerCanOverride:
    """Headers must use setdefault — route handlers may override per response."""

    def test_route_set_referrer_policy_wins(self) -> None:
        from starlette.responses import JSONResponse

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/custom")
        async def custom() -> JSONResponse:
            return JSONResponse(
                {"ok": "1"},
                headers={"Referrer-Policy": "no-referrer"},
            )

        client = TestClient(app)
        r = client.get("/custom")
        assert r.headers.get("Referrer-Policy") == "no-referrer"
