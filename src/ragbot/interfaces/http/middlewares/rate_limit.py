"""Per-endpoint sliding-window rate limit middleware (Layer-2).

Sits between :class:`TenantContextMiddleware` (outer) and
:class:`SecurityHeadersMiddleware` (inner). Operates per JWT token (or
composite ``record_tenant_id:user_id`` when JWT carries no jti) for each
configured endpoint policy. The coarse Layer-1 per-tenant limiter
(``TenantRateLimiter``) remains in front — different scope (tenant vs
token) so the layers compose, not replace.

Pre-auth requests (``/health``, ``/metrics``, ``/static/*``, Swagger
introspection) are routed past TenantContextMiddleware and have NO JWT
to key on; this middleware skips them via the policy table's
``unlimited`` entries. The IP-pre-auth limiter (Agent E) handles those
paths.

Headers
-------
On success: ``X-RateLimit-Limit / -Remaining / -Reset`` per W3C draft.
On 429: same trio plus ``Retry-After`` (seconds).

Application-MINDSET
-------------------
Pure transport-layer back-pressure. NO body / template / refusal text
injected — the JSON 429 body says only "rate limit exceeded".
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.application.ports.rate_limiter_port import RateLimiterPort
from ragbot.shared.constants import (
    DEFAULT_RL_EMIT_HEADERS,
    DEFAULT_RL_FAIL_MODE,
)
from ragbot.shared.rate_limit_policy import RateLimitPolicy, resolve_policy

logger = structlog.get_logger(__name__)


def _caller_key(request: Request) -> str | None:
    """Derive the per-token caller key from request state.

    Strategy:
      * Prefer JWT ``jti`` claim when carried (per-token granularity).
      * Else composite ``record_tenant_id:user_id`` (per-user-per-tenant).
      * Else fall back to bearer-token sha256 prefix (still distinct
        per token, just cheaper than a full hash).
      * Returns ``None`` when no auth context (caller is pre-auth — IP
        limiter handles those).
    """
    record_tenant_id = getattr(request.state, "record_tenant_id", None)
    user_id = getattr(request.state, "user_id", None)
    if record_tenant_id is not None and user_id is not None:
        return f"tok:{record_tenant_id!s}:{user_id}"
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            return f"tok:hash:{digest}"
    return None


class SlidingRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-endpoint per-token sliding-window rate limiter.

    Args:
        app: ASGI app.
        limiter: :class:`RateLimiterPort` implementation (Singleton from
            the DI container in production).
        fail_mode: ``"closed"`` returns 503 when the limiter raises a
            non-fail-open error (only InMemorySlidingWindow can; the
            Redis impl already fail-opens internally). ``"open"`` lets
            the request through.
        emit_headers: When True, attach W3C ``X-RateLimit-*`` headers to
            successful responses. Off only for tests that compare against
            golden response shapes.
    """

    def __init__(
        self,
        app: object,
        *,
        limiter: RateLimiterPort | None = None,
        fail_mode: str = DEFAULT_RL_FAIL_MODE,
        emit_headers: bool = DEFAULT_RL_EMIT_HEADERS,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        # ``limiter`` may be None at construction time; the dispatch will
        # then resolve via ``request.app.state.container.rate_limiter()``
        # so the lifespan-bound container is honoured (test fixtures
        # use a noop lifespan and override the container).
        self._limiter = limiter
        self._fail_mode = (fail_mode or "closed").strip().lower()
        self._emit_headers = bool(emit_headers)

    def _resolve_limiter(self, request: Request) -> RateLimiterPort | None:
        """Per-request limiter resolution.

        Order:
        1. Boot-time injected limiter (production fast path).
        2. ``request.app.state.container.rate_limiter()`` if the
           container exposes that provider.
        3. ``None`` → middleware passes through (test env without
           limiter wiring).
        """
        if self._limiter is not None:
            return self._limiter
        container = getattr(request.app.state, "container", None)
        if container is None:
            return None
        provider = getattr(container, "rate_limiter", None)
        if provider is None:
            return None
        try:
            candidate = provider()
        except (TypeError, AttributeError, RuntimeError):
            return None
        if isinstance(candidate, RateLimiterPort):
            return candidate
        return None

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Preflight is handled by CORSPerTenantMiddleware — never count
        # OPTIONS into rate-limit buckets.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        policy: RateLimitPolicy | None = resolve_policy(path)
        if policy is None:
            # Unlimited (health / metrics / static).
            return await call_next(request)
        if policy.limit <= 0:
            # Soft-unlimited explicit (op set 0 in constants).
            return await call_next(request)

        key = _caller_key(request)
        if key is None:
            # No auth context — pre-auth path is handled by IP limiter
            # (Agent E). Pass through.
            return await call_next(request)

        limiter = self._resolve_limiter(request)
        if limiter is None:
            # Test env / DI not wired → pass through. Production wiring
            # always supplies a limiter via ``app.add_middleware(...,
            # limiter=...)`` so this branch never fires there.
            return await call_next(request)

        # Compose per-endpoint key so /chat and /admin counters are
        # independent for the same caller.
        limiter_key = f"{key}:{request.method}:{path}"

        try:
            decision = await limiter.check(
                limiter_key,
                limit=policy.limit,
                window_s=policy.window_s,
                burst_factor=policy.burst_factor,
                burst_window_s=policy.burst_window_s,
            )
        except (RedisError, OSError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "sliding_rate_limit_backend_error",
                key=limiter_key,
                err=str(exc),
                error_type=type(exc).__name__,
            )
            if self._fail_mode == "open":
                return await call_next(request)
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "RATE_LIMIT_UNAVAILABLE",
                        "message": "rate-limit backend unavailable",
                    },
                },
                status_code=503,
            )

        if not decision.allowed:
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Rate limit exceeded",
                        "retry_after_s": decision.retry_after_s,
                    },
                },
                status_code=429,
                headers={
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(decision.reset_unix),
                    "Retry-After": str(decision.retry_after_s),
                },
            )

        if decision.source == "burst":
            logger.info(
                "rate_limit_burst_window_consumed",
                key=limiter_key,
                used=decision.used,
                limit=decision.limit,
            )

        response = await call_next(request)
        if self._emit_headers:
            response.headers.setdefault(
                "X-RateLimit-Limit", str(decision.limit),
            )
            response.headers.setdefault(
                "X-RateLimit-Remaining", str(decision.remaining),
            )
            response.headers.setdefault(
                "X-RateLimit-Reset", str(decision.reset_unix),
            )
        return response


def _safe_uuid(value: Any) -> UUID | None:
    """Best-effort UUID coercion (used by tests that synthesise state)."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = ("SlidingRateLimitMiddleware",)
