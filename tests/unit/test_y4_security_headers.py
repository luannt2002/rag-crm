"""Y4-SECURITY-MAX — security response headers (2026-05-01).

The middleware MUST attach the OWASP-baseline headers to every response
without overwriting handler-set values. Tests drive the dispatch directly
(no full FastAPI surface) so failures point at the middleware logic, not
route plumbing.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response

from ragbot.interfaces.http.middlewares.security_headers import (
    SecurityHeadersMiddleware,
)


def _make_request() -> Request:
    """Build a minimal ASGI Request — only headers are read by tests."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# §1 — baseline (HSTS off — dev default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_headers_appended_without_hsts() -> None:
    """X-CTO + X-FO + Referrer + CSP + Permissions emitted; HSTS absent."""
    mw = SecurityHeadersMiddleware(app=object(), hsts_enabled=False)

    async def call_next(_req: Request) -> Response:
        return Response(content=b"ok", status_code=200)

    resp = await mw.dispatch(_make_request(), call_next)

    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert "camera=()" in resp.headers["Permissions-Policy"]
    # HSTS opt-in only — must be absent under default dev config.
    assert "Strict-Transport-Security" not in resp.headers


# ---------------------------------------------------------------------------
# §2 — HSTS opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hsts_emitted_when_enabled() -> None:
    """When TLS terminates upstream, HSTS gets a 1-year directive."""
    mw = SecurityHeadersMiddleware(app=object(), hsts_enabled=True)

    async def call_next(_req: Request) -> Response:
        return Response(content=b"ok", status_code=200)

    resp = await mw.dispatch(_make_request(), call_next)
    hsts = resp.headers["Strict-Transport-Security"]
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


# ---------------------------------------------------------------------------
# §3 — handler-set values WIN (middleware uses setdefault)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_csp_not_overwritten() -> None:
    """A handler that sets its own CSP keeps it — middleware only fills gaps."""
    mw = SecurityHeadersMiddleware(app=object(), hsts_enabled=False)

    async def call_next(_req: Request) -> Response:
        r = Response(content=b"ok", status_code=200)
        r.headers["Content-Security-Policy"] = "default-src https://demo-cdn"
        return r

    resp = await mw.dispatch(_make_request(), call_next)
    # Handler value preserved.
    assert resp.headers["Content-Security-Policy"] == "default-src https://demo-cdn"
    # Other headers still fill in.
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# §4 — empty CSP suppresses header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_csp_omitted() -> None:
    """Operator that explicitly clears CSP gets no CSP header (escape hatch)."""
    mw = SecurityHeadersMiddleware(app=object(), hsts_enabled=False, csp="")

    async def call_next(_req: Request) -> Response:
        return Response(content=b"ok", status_code=200)

    resp = await mw.dispatch(_make_request(), call_next)
    assert "Content-Security-Policy" not in resp.headers
    # Other baseline headers still attach.
    assert resp.headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------------------
# §5 — non-2xx response (e.g. 401) still gets headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_response_still_secured() -> None:
    """401/403/500 paths must NOT bypass the security headers."""
    mw = SecurityHeadersMiddleware(app=object(), hsts_enabled=False)

    async def call_next(_req: Request) -> Response:
        return Response(content=b"unauthorized", status_code=401)

    resp = await mw.dispatch(_make_request(), call_next)
    assert resp.status_code == 401
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
