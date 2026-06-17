"""Honey-pot routes — passive attacker fingerprinting.

The configured paths (``/wp-admin``, ``/.env``, ``/admin/login.php``) are
classic vulnerability-scanner targets. Legitimate clients never link to
these from anywhere on our API surface, so any GET is by definition a
hostile probe. Every hit:

1. Logs ``honeypot_hit`` with ``{ip, ua, path}`` for SOC review.
2. Adds the source IP to the shared ``suspicious_ips`` set in Redis with
   :pydata:`DEFAULT_ANTI_ABUSE_HONEYPOT_TTL_S` TTL — the IP rate limit
   middleware applies the suspicious multiplier to that IP for 24h.
3. Returns 404 (not 403) so the scanner cannot distinguish honeypot from
   "endpoint missing" without behaviour analysis.

Domain-neutral
--------------
Path list is a constant (:pydata:`DEFAULT_HONEYPOT_PATHS`); no tenant or
brand literal. Operator may extend via constants edit + re-deploy — no
hot-config because honeypots must be invisible to runtime introspection.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from ragbot.shared.constants import (
    ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY,
    DEFAULT_ANTI_ABUSE_HONEYPOT_TTL_S,
    DEFAULT_HONEYPOT_PATHS,
)

logger = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)


async def _flag_suspicious(redis_client: Any, ip: str) -> None:
    """Add IP to the shared suspicious set with the honeypot TTL."""
    if not ip:
        return
    try:
        await redis_client.sadd(ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY, ip)
        await redis_client.expire(
            ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY,
            DEFAULT_ANTI_ABUSE_HONEYPOT_TTL_S,
        )
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        logger.debug("honeypot_redis_flag_skip", err=str(exc))


def _resolve_redis(request: Request) -> Any | None:
    try:
        container = request.app.state.container
        if container is None:
            return None
        redis_attr = getattr(container, "redis_client", None)
        return redis_attr() if redis_attr else None
    except (AttributeError, KeyError, TypeError):
        return None


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


async def _honeypot_handler(request: Request, path: str) -> JSONResponse:
    """Common 404-emitting handler that logs + flags the source IP."""
    ip = _client_ip(request)
    ua = (request.headers.get("User-Agent") or "")[:200]
    logger.warning("honeypot_hit", ip=ip, ua=ua, path=path)
    redis_client = _resolve_redis(request)
    if redis_client is not None:
        await _flag_suspicious(redis_client, ip)
    # Mimic FastAPI's default 404 body so the response is indistinguishable
    # from a genuine missing route.
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# Wire each honeypot path explicitly. Using add_api_route over a wildcard
# keeps the OpenAPI surface clean (include_in_schema=False) and ensures
# the path list is exactly the one in DEFAULT_HONEYPOT_PATHS.
for _path in DEFAULT_HONEYPOT_PATHS:
    async def _h(request: Request, _bound_path: str = _path) -> JSONResponse:
        return await _honeypot_handler(request, _bound_path)

    router.add_api_route(
        _path,
        _h,
        methods=["GET", "POST", "HEAD"],
        include_in_schema=False,
        name=f"honeypot_{_path.strip('/').replace('/', '_').replace('.', '_')}",
    )


__all__ = ["router"]
