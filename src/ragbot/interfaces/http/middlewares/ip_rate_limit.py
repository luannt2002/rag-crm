"""IP-based pre-auth rate limit middleware.

Caps requests per source IP **before** any authentication runs, so a
spray attacker with invalid bearers cannot burn DB / Redis cycles. This
middleware is *orthogonal* to the per-tenant token rate limiter
(:pymod:`ragbot.interfaces.http.middlewares.tenant_context`) — the
tenant limiter handles authenticated traffic, the IP limiter handles
the noise floor below auth.

Wiring
------
Insertion order in ``app.py`` matters: Starlette wraps middleware
**outside-in** in *reverse* insertion order, so the LAST ``add_middleware``
call is the OUTERMOST wrapper (runs FIRST per request). To run before
auth, this middleware is added LAST after ``BodySizeLimitMiddleware``.

Strategy
--------
1. Resolve the real source IP via the trusted-proxy chain
   (:pyfunc:`extract_real_ip`). Only honour ``X-Forwarded-For`` when the
   immediate ``request.client.host`` is in the operator's
   ``trusted_proxies`` allowlist; otherwise treat the connection peer
   as ground truth (defence vs. caller-spoofed XFF).
2. Bypass for health / metrics / static / docs paths and for IPs in
   ``ip_allowlist``.
3. Sliding-bucket counter in Redis: key
   ``ragbot:rl:ip:{ip}:{minute_bucket}`` — INCR + EXPIRE on first hit.
   Suspicious IPs (anti-abuse honeypot / 4xx-ratio flag) get the
   :pydata:`DEFAULT_ANTI_ABUSE_SUSPICIOUS_RL_MULTIPLIER` applied to the
   per-IP cap.
4. **Fail-CLOSED on Redis error** — anti-spray must not become a DoS
   amplifier under back-end outage. 503 + ``Retry-After`` returned;
   metric ``rate_limit_fail_closed_total{scope="ip"}`` ticked.
5. ``429`` response shape mirrors the existing rate-limit responses:
   ``{"ok": false, "error": {...}}`` plus ``Retry-After`` header. NO
   ``X-RateLimit-*`` headers — leaking the cap helps attackers tune
   request rates just below the line.

Domain-neutral / zero-hardcode
------------------------------
* Limits + window + bypass paths from ``shared/constants.py``.
* Trusted-proxy + allowlist from env (``APP_TRUSTED_PROXIES``,
  ``APP_IP_ALLOWLIST``).
* No tenant / brand literal anywhere.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.infrastructure.observability.metrics import (
    rate_limit_backend_error_total,
    rate_limit_fail_closed_total,
)
from ragbot.interfaces.http.middlewares.loadtest_bypass import is_loadtest_bypass
from ragbot.shared.constants import (
    ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY,
    DEFAULT_ANTI_ABUSE_SUSPICIOUS_RL_MULTIPLIER,
    DEFAULT_IP_RL_BYPASS_PATHS,
    DEFAULT_RL_IP_PER_MIN,
    DEFAULT_RL_IP_WINDOW_S,
)

logger = structlog.get_logger(__name__)

_IP_RL_PREFIX = "ragbot:rl:ip:"


def extract_real_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """Resolve the real source IP, honouring trusted XFF chains only.

    If the connection peer is in ``trusted_proxies``, walk the
    ``X-Forwarded-For`` chain right-to-left and return the **last entry
    that is NOT a trusted proxy** — that is the real client IP per the
    Mozilla / RFC 7239 advice.

    If the peer is NOT trusted, ignore XFF entirely (a hostile client
    can otherwise forge their source IP into the allowlist).

    Args:
        request: Starlette request.
        trusted_proxies: Frozenset of operator-trusted proxy IPs.

    Returns:
        Best-effort source IP; empty string if undetectable.
    """
    peer = (request.client.host if request.client else "") or ""
    if not peer:
        return ""
    if peer not in trusted_proxies:
        return peer
    xff = request.headers.get("X-Forwarded-For", "")
    if not xff:
        return peer
    # Right-most untrusted hop = real client.
    for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
        if hop not in trusted_proxies:
            return hop
    return peer


def _is_bypass_path(path: str) -> bool:
    """True iff ``path`` is on the IP-RL bypass list (health / metrics / static)."""
    for prefix in DEFAULT_IP_RL_BYPASS_PATHS:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


class IpRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP fixed-window rate limit, fail-closed on backend outage.

    Args:
        app: Starlette/FastAPI ASGI app.
        per_min: Cap per source IP per window.
        window_s: Window length in seconds (cap → reset after window).
        trusted_proxies: Iterable of trusted reverse-proxy IPs that may
            forward client IPs via ``X-Forwarded-For``.
        ip_allowlist: Iterable of IPs exempt from rate limiting (internal
            probes, monitoring, health-checkers).
        enabled: Master switch — operator may flip OFF on dev / canary.
    """

    def __init__(
        self,
        app: object,
        *,
        per_min: int = DEFAULT_RL_IP_PER_MIN,
        window_s: int = DEFAULT_RL_IP_WINDOW_S,
        trusted_proxies: frozenset[str] | None = None,
        ip_allowlist: frozenset[str] | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._per_min = per_min
        self._window_s = window_s
        self._trusted_proxies = trusted_proxies or frozenset()
        self._ip_allowlist = ip_allowlist or frozenset()
        self._enabled = enabled

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Apply per-IP rate limit; fail-closed on Redis error."""
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if _is_bypass_path(path):
            return await call_next(request)

        ip = extract_real_ip(request, self._trusted_proxies)
        if not ip or ip in self._ip_allowlist:
            return await call_next(request)

        # Localhost-only loadtest bypass — operator-issued token short-
        # circuits the per-IP cap without disabling auth or UA denylist.
        if is_loadtest_bypass(request):
            return await call_next(request)

        # `container_present=False` → app booted without lifespan (test env
        # using the noop lifespan — see tests/integration/test_p24_l3*).
        # `container_present=True && redis_client is None` → operator
        # misconfigured the DI container in production: fail-closed.
        container_present, redis_client = self._resolve_redis(request)
        if not container_present:
            # Lifespan never ran → not a production attacker scenario.
            # Auth, DB, and every other middleware is also non-functional;
            # nothing to defend.
            return await call_next(request)
        if redis_client is None:
            # Container present but the rate-limit backend is missing →
            # treat as outage. Anti-spray must not silently fail-open.
            rate_limit_fail_closed_total.labels(scope="ip").inc()
            logger.warning("ip_rate_limit_backend_missing", ip=ip)
            return self._fail_closed_response(self._window_s)

        try:
            suspicious = await self._is_suspicious(redis_client, ip)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            # Suspicion lookup failure is non-fatal — fall back to default cap.
            logger.debug("ip_rl_suspicion_lookup_skip", err=str(exc))
            suspicious = False

        effective_cap = self._per_min
        if suspicious:
            effective_cap = max(
                1,
                int(self._per_min * DEFAULT_ANTI_ABUSE_SUSPICIOUS_RL_MULTIPLIER),
            )

        try:
            count = await self._increment(redis_client, ip)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            rate_limit_backend_error_total.labels(reason="redis_error").inc()
            rate_limit_fail_closed_total.labels(scope="ip").inc()
            logger.warning("ip_rate_limit_redis_error", ip=ip, err=str(exc))
            return self._fail_closed_response(self._window_s)

        if count > effective_cap:
            logger.info(
                "ip_rate_limit_exceeded",
                ip=ip,
                count=count,
                cap=effective_cap,
                suspicious=suspicious,
                path=path,
            )
            return self._too_many_response(self._window_s)

        return await call_next(request)

    async def _increment(self, redis_client: Any, ip: str) -> int:
        """Increment the per-IP counter for the current window bucket."""
        now = int(time.time())
        bucket = now // self._window_s
        key = f"{_IP_RL_PREFIX}{ip}:{bucket}"
        count = await redis_client.incr(key)
        if count == 1:
            # +1 padding so a request landing at the very tail of a bucket
            # cannot read a counter that has already expired.
            await redis_client.expire(key, self._window_s + 1)
        return int(count)

    async def _is_suspicious(self, redis_client: Any, ip: str) -> bool:
        """Check honeypot-flagged IP set membership."""
        member = await redis_client.sismember(
            ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY, ip,
        )
        return bool(member)

    @staticmethod
    def _resolve_redis(request: Request) -> tuple[bool, Any | None]:
        """Pull the shared Redis client off the DI container.

        Returns ``(container_present, redis_client)``:
            * ``(False, None)`` — DI container itself missing (test env,
              ``state.container`` unset). Caller treats as no-op pass.
            * ``(True, None)`` — container present but ``redis_client``
              attribute missing or returns ``None``. Caller treats as
              outage and fails closed.
            * ``(True, <client>)`` — operating normally.
        """
        try:
            container = request.app.state.container
        except AttributeError:
            return (False, None)
        if container is None:
            return (False, None)
        try:
            redis_attr = getattr(container, "redis_client", None)
            if redis_attr is None:
                return (True, None)
            client = redis_attr()
            return (True, client)
        except (KeyError, TypeError):
            return (True, None)

    @staticmethod
    def _too_many_response(retry_after_s: int) -> JSONResponse:
        """429 Too Many Requests — no rate-limit reveal headers (anti-tune)."""
        return JSONResponse(
            {
                "ok": False,
                "data": None,
                "error": {
                    "code": "IP_RATE_LIMITED",
                    "message": "request rate limit exceeded for source IP",
                    "details": {},
                },
            },
            status_code=429,
            headers={"Retry-After": str(retry_after_s)},
        )

    @staticmethod
    def _fail_closed_response(retry_after_s: int) -> JSONResponse:
        """503 — rate-limit backend down; refuse rather than admit unmetered."""
        return JSONResponse(
            {
                "ok": False,
                "data": None,
                "error": {
                    "code": "RATE_LIMIT_UNAVAILABLE",
                    "message": "rate-limit backend unavailable",
                    "details": {},
                },
            },
            status_code=503,
            headers={"Retry-After": str(retry_after_s)},
        )


__all__ = ["IpRateLimitMiddleware", "extract_real_ip"]
