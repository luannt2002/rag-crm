"""Security response headers middleware (Y4 — 2026-05-01).

Adds OWASP-baseline response headers to every HTTP response. Wires
**innermost** (last add → outermost wrap by Starlette, but appended to
response on the way out), so headers attach regardless of which route or
middleware short-circuits the request.

Headers emitted
---------------
* ``X-Content-Type-Options: nosniff`` — disable MIME sniffing.
* ``X-Frame-Options: DENY`` — disable iframing of API origin.
* ``Referrer-Policy: strict-origin-when-cross-origin`` — drop path/query
  from cross-origin Referer.
* ``Strict-Transport-Security`` — HSTS 1 year, subdomains, preload-ready.
  ONLY emitted when ``hsts_enabled=True`` (TLS-terminated environments) —
  HTTP-only dev environments must NOT advertise HSTS or browsers will
  refuse to load over HTTP next visit.
* ``Content-Security-Policy`` — default-deny; explicitly allow ``self`` +
  the static dir for the demo pages. Configurable via ``csp`` ctor arg.

Domain-neutral
--------------
No tenant / brand literal. CSP origin list is operator-supplied via env;
defaults to ``'self'`` only.

Zero-hardcode
-------------
Default values land in ``shared/constants.py`` as
``DEFAULT_SECURITY_HEADERS_*`` so operators can override per environment
without touching code.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ragbot.shared.constants import (
    DEFAULT_SECURITY_HEADERS_COEP_DOCS_ONLY,
    DEFAULT_SECURITY_HEADERS_COEP_PATHS,
    DEFAULT_SECURITY_HEADERS_COOP,
    DEFAULT_SECURITY_HEADERS_CORP,
    DEFAULT_SECURITY_HEADERS_CSP,
    DEFAULT_SECURITY_HEADERS_HSTS_VALUE,
    DEFAULT_SECURITY_HEADERS_PERMISSIONS_POLICY,
    DEFAULT_SECURITY_HEADERS_PERMITTED_CROSS_DOMAIN,
    DEFAULT_SECURITY_HEADERS_REFERRER_POLICY,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append OWASP-baseline response headers.

    Args:
        app: Starlette/FastAPI ASGI app.
        hsts_enabled: emit ``Strict-Transport-Security`` header. Set
            ``True`` only when the deployment terminates TLS — emitting
            HSTS over plain HTTP locks browsers out of dev environments.
        csp: Content-Security-Policy string. Default = restrictive
            self-only; relax via env / system_config for demo pages
            that load CDN assets.
        referrer_policy: ``Referrer-Policy`` value.
        hsts_value: ``Strict-Transport-Security`` directive (only used
            when ``hsts_enabled``).
        permissions_policy: ``Permissions-Policy`` directive (default =
            disable camera/microphone/geolocation for the API origin).
    """

    def __init__(
        self,
        app: object,
        *,
        hsts_enabled: bool = False,
        csp: str = DEFAULT_SECURITY_HEADERS_CSP,
        referrer_policy: str = DEFAULT_SECURITY_HEADERS_REFERRER_POLICY,
        hsts_value: str = DEFAULT_SECURITY_HEADERS_HSTS_VALUE,
        permissions_policy: str = DEFAULT_SECURITY_HEADERS_PERMISSIONS_POLICY,
        coop: str = DEFAULT_SECURITY_HEADERS_COOP,
        corp: str = DEFAULT_SECURITY_HEADERS_CORP,
        coep_docs_only: str = DEFAULT_SECURITY_HEADERS_COEP_DOCS_ONLY,
        coep_paths: tuple[str, ...] = DEFAULT_SECURITY_HEADERS_COEP_PATHS,
        permitted_cross_domain: str = DEFAULT_SECURITY_HEADERS_PERMITTED_CROSS_DOMAIN,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._hsts_enabled = hsts_enabled
        self._csp = csp
        self._referrer_policy = referrer_policy
        self._hsts_value = hsts_value
        self._permissions_policy = permissions_policy
        self._coop = coop
        self._corp = corp
        self._coep_docs_only = coep_docs_only
        self._coep_paths = coep_paths
        self._permitted_cross_domain = permitted_cross_domain

    def _coep_applies(self, path: str) -> bool:
        """True iff the request path matches a configured COEP-eligible route."""
        for pattern in self._coep_paths:
            if path == pattern or path.startswith(f"{pattern}/"):
                return True
        return False

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        # Append (don't overwrite): a route handler can set its own value
        # — middleware only fills the gap if the route did not.
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", self._referrer_policy)
        if self._csp:
            h.setdefault("Content-Security-Policy", self._csp)
        if self._permissions_policy:
            h.setdefault("Permissions-Policy", self._permissions_policy)
        if self._coop:
            h.setdefault("Cross-Origin-Opener-Policy", self._coop)
        if self._corp:
            h.setdefault("Cross-Origin-Resource-Policy", self._corp)
        if self._permitted_cross_domain:
            h.setdefault(
                "X-Permitted-Cross-Domain-Policies",
                self._permitted_cross_domain,
            )
        # COEP only on opt-in paths — broader enforcement breaks browser
        # POST CORS for the demo widget against third-party origins.
        if self._coep_docs_only and self._coep_applies(request.url.path):
            h.setdefault("Cross-Origin-Embedder-Policy", self._coep_docs_only)
        if self._hsts_enabled and self._hsts_value:
            # ONLY when TLS is terminated upstream — emitting HSTS over
            # plain HTTP locks dev browsers out for the directive's TTL.
            h.setdefault("Strict-Transport-Security", self._hsts_value)
        return response


__all__ = ["SecurityHeadersMiddleware"]
