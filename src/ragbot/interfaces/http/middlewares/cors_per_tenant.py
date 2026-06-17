"""Per-tenant CORS strict whitelist middleware.

Replaces the global ``starlette.middleware.cors.CORSMiddleware`` with a
per-tenant ASGI middleware that consults ``tenants.allowed_origins``
(JSONB) at request time. Supports exact origins
(``https://app.example.com``) and wildcard host patterns
(``https://*.example.com``).

Wiring order
------------
``TenantContextMiddleware`` (outer; runs first on request) lifts
``request.state.record_tenant_id`` from the JWT bearer. This middleware
sits inner and reads that value to look up the per-tenant whitelist via
``TenantConfigCache``.

For routes that bypass the auth middleware (``/health``, ``/metrics``,
``/static/*``, Swagger), this middleware falls back to the global
``settings.app.cors_allowed_origins`` env list — same contract as the
old global ``CORSMiddleware``.

Domain-neutral
--------------
No tenant / brand literal. Allowed origins are operator-provided per
tenant via ``PATCH /admin/tenants/{id}`` (admin CRUD).

Application-MINDSET
-------------------
This is pure transport-layer wiring. No prompt / answer / template
injection — bot owners do not see CORS state.
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.application.services.tenant_config_cache import TenantConfigCache
from ragbot.shared.constants import (
    DEFAULT_CORS_ALLOW_HEADERS,
    DEFAULT_CORS_ALLOW_METHODS,
    DEFAULT_CORS_PREFLIGHT_MAX_AGE_S,
)

logger = structlog.get_logger(__name__)


# Routes that bypass tenant authentication (TenantContextMiddleware also
# treats these as public). For these we fall back to the global env CORS
# list — same surface as the old global CORSMiddleware.
_PRE_AUTH_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/metrics",
    "/static/",
    "/demo-ragbot",
    "/openapi",
    "/docs",
    "/redoc",
)


def _is_pre_auth_path(path: str) -> bool:
    """Return True when the path is served before TenantContextMiddleware."""
    return any(path == p or path.startswith(p) for p in _PRE_AUTH_PATH_PREFIXES)


def origin_matches(origin: str, allowed: tuple[str, ...] | list[str]) -> bool:
    """Match an Origin header against a tenant's whitelist.

    Two pattern shapes:

    * Exact origin: ``https://app.example.com`` matches only that string.
    * Wildcard host: ``https://*.example.com`` matches
      ``https://anything.example.com`` but NOT ``https://example.com``
      itself (subdomain only) and NOT ``https://other.example.org``.

    The wildcard restriction to a single ``*`` in the host part is
    deliberate — full glob would let an operator misconfigure
    ``https://*`` and accept everything. Scheme + suffix are matched
    literally.
    """
    if not origin or not allowed:
        return False
    for pattern in allowed:
        if not pattern:
            continue
        if pattern == "*":
            # Operator opt-in to allow-all (dev only). We honour it but the
            # operator should NOT use this in production — surfaced via
            # boot warning at create_app time.
            return True
        if pattern == origin:
            return True
        # Wildcard host pattern: "https://*.example.com"
        if "://*." in pattern:
            scheme, rest = pattern.split("://*.", 1)
            prefix = f"{scheme}://"
            if not origin.startswith(prefix):
                continue
            host_part = origin[len(prefix):]
            # host_part must end with "." + rest AND must have a non-empty
            # subdomain segment before the dot.
            needle = f".{rest}"
            if host_part.endswith(needle) and len(host_part) > len(needle):
                return True
    return False


class CORSPerTenantMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that emits per-tenant CORS headers.

    Lifecycle:
      1. Read ``Origin`` header. Absent → pass through (non-browser /
         same-origin caller).
      2. For pre-auth paths: match against the global env list.
      3. For tenant-scoped paths: read ``request.state.record_tenant_id``
         (lifted by ``TenantContextMiddleware``); look up
         ``TenantConfigCache.get(record_tenant_id).allowed_origins``.
      4. Preflight OPTIONS:
         * Match → 204 with ACAO + ACAM + ACAH + ACAC + Max-Age.
         * Miss → 403 (do NOT emit ACAO).
      5. Non-preflight: pass through; if origin matches, attach ACAO +
         ACAC on the response. Browser blocks the read otherwise.

    Args:
        app: ASGI app.
        global_origins: Fallback whitelist for pre-auth paths (env-driven).
        max_age_s: ``Access-Control-Max-Age`` value for preflight responses.
    """

    def __init__(
        self,
        app: object,
        *,
        global_origins: tuple[str, ...] = (),
        max_age_s: int = DEFAULT_CORS_PREFLIGHT_MAX_AGE_S,
        allow_methods: tuple[str, ...] = DEFAULT_CORS_ALLOW_METHODS,
        allow_headers: tuple[str, ...] = DEFAULT_CORS_ALLOW_HEADERS,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._global_origins = tuple(global_origins)
        self._max_age_s = int(max_age_s)
        self._allow_methods = ", ".join(allow_methods)
        self._allow_headers = ", ".join(allow_headers)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        origin = request.headers.get("origin", "")
        if not origin:
            # No Origin header → not a browser CORS request. Pass through.
            return await call_next(request)

        is_preflight = (
            request.method == "OPTIONS"
            and "access-control-request-method" in request.headers
        )
        path = request.url.path

        allowed = await self._resolve_allowed(request, path)

        if is_preflight:
            if origin_matches(origin, allowed):
                return self._preflight_204(origin)
            logger.info(
                "cors_origin_rejected",
                origin=origin,
                path=path,
                record_tenant_id=str(
                    getattr(request.state, "record_tenant_id", None) or "",
                ),
            )
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "cors_origin_rejected",
                        "message": "origin not in tenant whitelist",
                    },
                },
                status_code=403,
            )

        response = await call_next(request)
        if origin_matches(origin, allowed):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            # Vary: Origin so caches do not serve a different tenant's
            # ACAO header from the same path/method bucket.
            existing_vary = response.headers.get("Vary", "")
            if "origin" not in existing_vary.lower():
                response.headers["Vary"] = (
                    f"{existing_vary}, Origin" if existing_vary else "Origin"
                )
        return response

    def _preflight_204(self, origin: str) -> Response:
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": self._allow_methods,
                "Access-Control-Allow-Headers": self._allow_headers,
                "Access-Control-Max-Age": str(self._max_age_s),
                "Vary": "Origin",
            },
        )

    async def _resolve_allowed(
        self, request: Request, path: str,
    ) -> tuple[str, ...]:
        """Resolve the effective allow-list for the request.

        Pre-auth path → global env list. Tenant path → cache lookup with
        graceful fallback to empty (deny all) on cache / DB error so the
        deny-by-default contract holds.
        """
        if _is_pre_auth_path(path):
            return self._global_origins

        record_tenant_id = getattr(request.state, "record_tenant_id", None)
        if record_tenant_id is None:
            # Auth not yet performed for this path — fall back to global
            # list (e.g. unauthenticated POST /admin/auth). Safer to use
            # the platform-operator-provided env list than to deny.
            return self._global_origins

        try:
            tid = (
                record_tenant_id
                if isinstance(record_tenant_id, UUID)
                else UUID(str(record_tenant_id))
            )
        except (TypeError, ValueError):
            return self._global_origins

        cache = self._get_cache(request)
        if cache is None:
            return ()  # Deny — cache wiring missing.
        try:
            cfg = await cache.get(tid)
        except (RedisError, OSError, asyncio.TimeoutError, ValueError) as exc:
            logger.warning(
                "cors_per_tenant_cache_lookup_failed",
                err=str(exc),
                error_type=type(exc).__name__,
            )
            return ()  # Deny on cache error.
        if cfg is None:
            return ()  # Tenant has no row — deny.
        return tuple(cfg.allowed_origins or ())

    @staticmethod
    def _get_cache(request: Request) -> TenantConfigCache | None:
        """Best-effort container lookup; returns None when bootstrap missing."""
        container = getattr(request.app.state, "container", None)
        if container is None:
            return None
        try:
            return container.tenant_config_cache()
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.debug("cors_per_tenant_container_missing", err=str(exc))
            return None


def parse_global_origins(raw: str | None) -> tuple[str, ...]:
    """Parse the ``APP_CORS_ALLOWED_ORIGINS`` JSON-array string env value.

    Mirrors the old global CORSMiddleware bootstrap so deployments can
    keep their existing env wiring without change. Invalid JSON or
    non-list collapses to empty tuple (deny).
    """
    if not raw:
        return ()
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(str(x) for x in data if isinstance(x, str))


__all__: tuple[str, ...] = (
    "CORSPerTenantMiddleware",
    "origin_matches",
    "parse_global_origins",
)
