"""Anti-abuse middleware.

Layered cheap-heuristic defences against bots, scrapers, and pre-auth
spray attacks. Designed to short-circuit obvious abuse with sub-5ms
overhead per request and to feed structured signals into the IP rate
limiter (suspicious-IP set → tighter cap).

Defences
--------
1. **User-Agent denylist** (substring, lowercased). Skipped for paths in
   :pydata:`DEFAULT_IP_RL_BYPASS_PATHS` and bypassed for callers
   presenting a valid ``X-API-Key`` whose SHA-256 is in the operator's
   programmatic-key allowlist.
2. **Authentication-failure ban**. We *observe* the response status from
   ``call_next`` — when it is 401 / 403 we increment a per-IP failure
   counter; once the counter exceeds
   :pydata:`DEFAULT_ANTI_ABUSE_AUTH_FAIL_THRESHOLD` within
   :pydata:`DEFAULT_ANTI_ABUSE_AUTH_FAIL_WINDOW_S` the IP is banned for
   :pydata:`DEFAULT_ANTI_ABUSE_BAN_DURATION_S`. While banned, every
   request short-circuits to 429.
3. **Distinct-paths-per-minute** soft throttle. Track the unique paths
   each IP touches per minute; over the threshold flags "scanner-like"
   behaviour and applies a 1s artificial delay (not a hard reject — real
   spider-pattern traffic from search bots already filtered by UA).
4. **4xx-rate flag**. Rolling ratio of 4xx vs total over the last
   :pydata:`DEFAULT_ANTI_ABUSE_4XX_WINDOW_REQUESTS` requests; over the
   threshold logs ``anti_abuse_4xx_high_rate`` and adds the IP to the
   suspicious set so the IP rate limiter applies the multiplier.

Wiring
------
Added INSIDE ``IpRateLimitMiddleware`` (i.e. inserted BEFORE it in
``app.py``) so the IP RL gets first crack at obvious flooders, but this
middleware still runs ahead of auth so failed-auth attempts feed the
ban counter.

Domain-neutral / zero-hardcode
------------------------------
* All thresholds + windows + denylist defaults from ``shared/constants``.
* UA denylist + programmatic-key allowlist override via env.
* No tenant or vendor literals.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import structlog
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.interfaces.http.middlewares.ip_rate_limit import (
    _is_bypass_path,
    extract_real_ip,
)
from ragbot.interfaces.http.middlewares.loadtest_bypass import is_loadtest_bypass
from ragbot.shared.constants import (
    ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY,
    DEFAULT_ANTI_ABUSE_4XX_RATIO_THRESHOLD,
    DEFAULT_ANTI_ABUSE_4XX_WINDOW_REQUESTS,
    DEFAULT_ANTI_ABUSE_AUTH_FAIL_THRESHOLD,
    DEFAULT_ANTI_ABUSE_AUTH_FAIL_WINDOW_S,
    DEFAULT_ANTI_ABUSE_BAN_DURATION_S,
    DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_PER_MIN,
    DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_TTL_PADDING_S,
    DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_WINDOW_S,
    DEFAULT_ANTI_ABUSE_HONEYPOT_TTL_S,
    DEFAULT_UA_DENYLIST_ENFORCED_PREFIXES,
    DEFAULT_UA_DENYLIST_PATTERNS,
)

logger = structlog.get_logger(__name__)

# Redis key prefixes — namespace under ragbot:antiabuse:* so an operator
# can debug / flush them in one wildcard.
_NS = "ragbot:antiabuse:"
_KEY_AUTH_FAIL = _NS + "authfail:"
_KEY_BAN = _NS + "ban:"
_KEY_PATHS = _NS + "paths:"
_KEY_4XX_TOTAL = _NS + "4xx_total:"
_KEY_4XX_FAIL = _NS + "4xx_fail:"
# Re-export so callers can `from anti_abuse import SUSPICIOUS_IP_SET` —
# the canonical literal lives in shared/constants and is enforced equal.
SUSPICIOUS_IP_SET = ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY


def _hash_api_key(raw_key: str) -> str:
    """SHA-256 hex of the raw key (single round; same as the env format)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class AntiAbuseMiddleware(BaseHTTPMiddleware):
    """Composite anti-bot / anti-spray defence."""

    def __init__(
        self,
        app: object,
        *,
        ua_denylist: tuple[str, ...] = DEFAULT_UA_DENYLIST_PATTERNS,
        ua_denylist_enforced_prefixes: tuple[str, ...] = DEFAULT_UA_DENYLIST_ENFORCED_PREFIXES,
        programmatic_key_hashes: frozenset[str] | None = None,
        trusted_proxies: frozenset[str] | None = None,
        ip_allowlist: frozenset[str] | None = None,
        auth_fail_threshold: int = DEFAULT_ANTI_ABUSE_AUTH_FAIL_THRESHOLD,
        auth_fail_window_s: int = DEFAULT_ANTI_ABUSE_AUTH_FAIL_WINDOW_S,
        ban_duration_s: int = DEFAULT_ANTI_ABUSE_BAN_DURATION_S,
        distinct_paths_per_min: int = DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_PER_MIN,
        fourxx_ratio_threshold: float = DEFAULT_ANTI_ABUSE_4XX_RATIO_THRESHOLD,
        fourxx_window_requests: int = DEFAULT_ANTI_ABUSE_4XX_WINDOW_REQUESTS,
        honeypot_ttl_s: int = DEFAULT_ANTI_ABUSE_HONEYPOT_TTL_S,
        soft_throttle_delay_s: float = 1.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._ua_denylist = tuple(p.lower() for p in ua_denylist if p)
        self._ua_denylist_enforced_prefixes = tuple(
            p for p in ua_denylist_enforced_prefixes if p
        )
        self._programmatic_key_hashes = programmatic_key_hashes or frozenset()
        self._trusted_proxies = trusted_proxies or frozenset()
        self._ip_allowlist = ip_allowlist or frozenset()
        self._auth_fail_threshold = auth_fail_threshold
        self._auth_fail_window_s = auth_fail_window_s
        self._ban_duration_s = ban_duration_s
        self._distinct_paths_per_min = distinct_paths_per_min
        self._fourxx_ratio_threshold = fourxx_ratio_threshold
        self._fourxx_window_requests = fourxx_window_requests
        self._honeypot_ttl_s = honeypot_ttl_s
        self._soft_throttle_delay_s = soft_throttle_delay_s
        self._enabled = enabled
        # TODO: extract AntiAbusePort + composable check list once the
        # check count grows beyond the four below — for the current surface
        # the flat dispatch keeps trace overhead minimal.

    async def dispatch(  # noqa: C901 — orchestration sequence is intrinsically branchy
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if _is_bypass_path(path):
            return await call_next(request)

        ip = extract_real_ip(request, self._trusted_proxies)
        if not ip or ip in self._ip_allowlist:
            return await call_next(request)

        ua = (request.headers.get("User-Agent") or "").lower()
        api_key_header = request.headers.get("X-API-Key", "").strip()
        api_key_ok = bool(
            api_key_header
            and _hash_api_key(api_key_header) in self._programmatic_key_hashes
        )

        # 1. UA denylist (cheapest check, no Redis hit).
        # Scoped to user-conversation hot endpoints only; ops/admin/auth
        # paths rely on RBAC + IP rate-limit + auth-fail ban.
        ua_enforce = self._ua_enforced_for_path(path)
        if ua_enforce and not api_key_ok and self._ua_denied(ua):
            logger.info(
                "ua_denied",
                ip=ip,
                ua=ua[:200],
                path=path,
            )
            return JSONResponse(
                {
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": "FORBIDDEN_USER_AGENT",
                        "message": "user-agent not permitted",
                        "details": {},
                    },
                },
                status_code=403,
            )

        redis_client = self._resolve_redis(request)
        if redis_client is None:
            # Redis missing: skip the stateful checks but still let the
            # request through — the IP rate limiter (which DOES fail-
            # closed) is the harder backstop.
            return await call_next(request)

        # 2. Banned-IP short-circuit.
        if await self._is_banned(redis_client, ip):
            logger.info("anti_abuse_banned_ip_blocked", ip=ip, path=path)
            return JSONResponse(
                {
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": "TEMPORARILY_BANNED",
                        "message": (
                            "source IP is temporarily blocked due to "
                            "abusive behaviour"
                        ),
                        "details": {},
                    },
                },
                status_code=429,
                headers={"Retry-After": str(self._ban_duration_s)},
            )

        # 3. Distinct-paths-per-minute scanner heuristic (soft throttle).
        try:
            distinct_count = await self._record_path(redis_client, ip, path)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.debug("anti_abuse_path_record_skip", err=str(exc))
            distinct_count = 0
        if distinct_count > self._distinct_paths_per_min:
            logger.info(
                "anti_abuse_path_scanner_soft_throttle",
                ip=ip,
                distinct=distinct_count,
                path=path,
            )
            await asyncio.sleep(self._soft_throttle_delay_s)

        # Forward request, observe status.
        response = await call_next(request)

        loadtest_bypass = is_loadtest_bypass(request)

        status = response.status_code
        # 4. Auth-fail counter — feed ban.
        # Loadtest bypass skips this branch so an operator-issued benchmark
        # cannot ban its own loopback caller (mirror of rule 5 below).
        if status in (401, 403) and not loadtest_bypass:
            try:
                fail_count = await self._record_auth_fail(redis_client, ip)
            except (RedisError, OSError, asyncio.TimeoutError) as exc:
                logger.debug("anti_abuse_authfail_record_skip", err=str(exc))
                fail_count = 0
            if fail_count >= self._auth_fail_threshold:
                try:
                    await self._ban(redis_client, ip)
                    logger.warning(
                        "anti_abuse_ip_banned",
                        ip=ip,
                        fails=fail_count,
                        ban_s=self._ban_duration_s,
                    )
                except (RedisError, OSError, asyncio.TimeoutError) as exc:
                    logger.debug("anti_abuse_ban_set_skip", err=str(exc))

        # 5. 4xx-ratio rolling — flag on threshold breach (do not reject;
        # the suspicious flag tightens the IP rate limiter on next req).
        # Loadtest bypass skips this branch so a synthetic probe storm
        # cannot self-flag the loopback caller as suspicious.
        if loadtest_bypass:
            return response
        if 400 <= status < 500:
            try:
                ratio = await self._record_4xx(redis_client, ip, is_fail=True)
            except (RedisError, OSError, asyncio.TimeoutError) as exc:
                logger.debug("anti_abuse_4xx_record_skip", err=str(exc))
                ratio = 0.0
            if ratio >= self._fourxx_ratio_threshold:
                logger.warning(
                    "anti_abuse_4xx_high_rate",
                    ip=ip,
                    ratio=round(ratio, 3),
                )
                try:
                    await self._mark_suspicious(redis_client, ip)
                except (RedisError, OSError, asyncio.TimeoutError) as exc:
                    logger.debug(
                        "anti_abuse_mark_suspicious_skip", err=str(exc),
                    )
        else:
            try:
                await self._record_4xx(redis_client, ip, is_fail=False)
            except (RedisError, OSError, asyncio.TimeoutError) as exc:
                logger.debug("anti_abuse_4xx_record_skip", err=str(exc))

        return response

    # ----- helpers ----------------------------------------------------------

    def _ua_denied(self, ua_lower: str) -> bool:
        if not ua_lower:
            # Empty UA is itself suspicious — most legitimate browsers send one.
            # Reject as denied.
            return True
        return any(pattern in ua_lower for pattern in self._ua_denylist)

    def _ua_enforced_for_path(self, path: str) -> bool:
        if not self._ua_denylist_enforced_prefixes:
            return False
        return any(
            path.startswith(prefix)
            for prefix in self._ua_denylist_enforced_prefixes
        )

    @staticmethod
    async def _is_banned(redis_client: Any, ip: str) -> bool:
        val = await redis_client.get(f"{_KEY_BAN}{ip}")
        return val is not None

    async def _ban(self, redis_client: Any, ip: str) -> None:
        await redis_client.set(
            f"{_KEY_BAN}{ip}", "1", ex=self._ban_duration_s,
        )
        # Also mark suspicious so the IP RL applies the multiplier even
        # after the hard ban window expires.
        await self._mark_suspicious(redis_client, ip)

    async def _mark_suspicious(self, redis_client: Any, ip: str) -> None:
        await redis_client.sadd(SUSPICIOUS_IP_SET, ip)
        await redis_client.expire(SUSPICIOUS_IP_SET, self._honeypot_ttl_s)

    async def _record_auth_fail(self, redis_client: Any, ip: str) -> int:
        """Increment failure counter for current window; return current value."""
        now = int(time.time())
        bucket = now // self._auth_fail_window_s
        key = f"{_KEY_AUTH_FAIL}{ip}:{bucket}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, self._auth_fail_window_s + 1)
        return int(count)

    async def _record_path(self, redis_client: Any, ip: str, path: str) -> int:
        """Add path to per-IP minute set; return cardinality after add."""
        now = int(time.time())
        # Rolling window — separate from auth_fail_window for tunability.
        window = DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_WINDOW_S
        bucket = now // window
        key = f"{_KEY_PATHS}{ip}:{bucket}"
        await redis_client.sadd(key, path)
        await redis_client.expire(
            key, window + DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_TTL_PADDING_S,
        )
        card = await redis_client.scard(key)
        return int(card)

    async def _record_4xx(
        self,
        redis_client: Any,
        ip: str,
        *,
        is_fail: bool,
    ) -> float:
        """Increment rolling counters; return current 4xx/total ratio."""
        total_key = f"{_KEY_4XX_TOTAL}{ip}"
        fail_key = f"{_KEY_4XX_FAIL}{ip}"
        # 5x window TTL so a quiet IP's counters age out before they bias
        # the next session's classification.
        ttl = self._auth_fail_window_s * 5
        total = await redis_client.incr(total_key)
        if total == 1:
            await redis_client.expire(total_key, ttl)
        fails = 0
        if is_fail:
            fails = await redis_client.incr(fail_key)
            if fails == 1:
                await redis_client.expire(fail_key, ttl)
        else:
            raw = await redis_client.get(fail_key)
            try:
                fails = int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                fails = 0
        # Only evaluate ratio after the rolling window has matured —
        # otherwise a single 401 at request 2 trips the flag.
        if int(total) < self._fourxx_window_requests:
            return 0.0
        if int(total) <= 0:
            return 0.0
        return float(fails) / float(total)

    @staticmethod
    def _resolve_redis(request: Request) -> Any | None:
        try:
            container = request.app.state.container
            if container is None:
                return None
            redis_attr = getattr(container, "redis_client", None)
            if redis_attr is None:
                return None
            return redis_attr()
        except (AttributeError, KeyError, TypeError):
            return None


__all__ = ["AntiAbuseMiddleware", "SUSPICIOUS_IP_SET"]
