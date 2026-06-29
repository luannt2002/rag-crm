"""Per-4-key bot rate limit middleware (multi-tenant fairness — 2026-05-16).

Sits AFTER :class:`TenantContextMiddleware` (so ``record_tenant_id`` is
bound onto ``request.state``) and AFTER :class:`SlidingRateLimitMiddleware`
(per-token cap composes — both layers must pass). Operates on the
**internal 4-key bot identity**::

    (record_tenant_id, workspace_id, bot_id, channel_type)

so two tenants × two workspaces that both name a bot ``"support"`` on
channel ``"web"`` get **independent** rate-limit buckets — exactly the
isolation multi-tenant SaaS contract requires.

Why a separate layer
--------------------
The pre-existing :class:`SlidingRateLimitMiddleware` keys on
``record_tenant_id:user_id`` (per-token-per-user). It cannot catch the
case where one tenant's "support" bot ingests 1000 docs and starves
another tenant's "support" bot — both share a token? Different tokens,
same tenant, different workspace? The 4-key tuple is the only safe
fairness boundary. Stacking middlewares is cheaper than rewriting the
existing one.

Where the bot identity comes from
---------------------------------
Two extraction strategies (first match wins):

1. ``request.state.bot_identity`` — set by routes that resolved a bot
   via ``BotRegistryService.lookup(...)``. Carries all 4 keys.
2. Path parsing for routes that follow the canonical
   ``/api/ragbot/test/bots/{bot_id}/{channel_type}/...`` convention. The
   ``record_tenant_id`` lift from JWT (request.state) + ``workspace_id``
   from header / body / fallback to ``str(record_tenant_id)``.

When neither strategy yields all 4 keys, the request **bypasses** this
layer (per-token + per-IP still apply). Logging the bypass surfaces
mis-wired routes that ops can fix.

Degrade-open on Redis error
---------------------------
INCR / EXPIRE on the Redis hot path. Connection / timeout error logs
``bot_rate_limit_redis_failed`` and passes the request through —
upstream layers retain the burst defence.

Headers emitted on success
--------------------------
- ``X-RateLimit-Bot-Limit``: configured per-window cap
- ``X-RateLimit-Bot-Window``: window size in seconds
- ``X-RateLimit-Bot-Remaining``: requests left in this window
- ``X-RateLimit-Bot-Reset``: seconds until counter resets

On 429:

- All the headers above PLUS ``Retry-After`` (seconds).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.interfaces.http.middlewares.loadtest_bypass import is_loadtest_bypass
from ragbot.shared.constants import (
    DEFAULT_RL_BOT_PER_MIN,
    DEFAULT_RL_UPLOAD_PER_MIN,
    DEFAULT_RL_WINDOW_S,
)

logger = structlog.get_logger(__name__)

_BOT_RL_PREFIX = "ragbot:rl:bot:"
# Bot identity surfaces on canonical chat / upload routes. Patterns
# anchor on the api_base_path prefix; matching is best-effort —
# routes that don't follow the convention bypass this layer.
_BOT_PATH_RE = re.compile(
    r"^/api/ragbot/test/bots/"
    r"(?P<bot_id>[^/]+)/(?P<channel_type>[^/]+)(?:/.*)?$"
)
# Ingest paths get a tighter cap because each request kicks an
# expensive worker pipeline (parse → chunk → embed → DB insert).
_INGEST_HINT_RE = re.compile(r"/ingest|/upload|/sync|/documents", re.IGNORECASE)


def _is_ingest_path(path: str) -> bool:
    """Heuristic — ingest paths get the upload-tight cap."""
    return bool(_INGEST_HINT_RE.search(path))


def _resolve_bot_identity(request: Request) -> tuple[str, str, str, str] | None:
    """Extract the 4-key tuple ``(tenant, workspace, bot_id, channel_type)``.

    Returns ``None`` when any key is missing — caller must bypass.
    """
    # 1. Pre-resolved identity (set by routes that called BotRegistry).
    identity = getattr(request.state, "bot_identity", None)
    if identity is not None:
        tenant = identity.get("record_tenant_id")
        workspace = identity.get("workspace_id")
        bot_id = identity.get("bot_id")
        channel = identity.get("channel_type")
        if tenant and workspace and bot_id and channel:
            return (str(tenant), str(workspace), str(bot_id), str(channel))

    # 2. Path parsing — pull bot_id + channel_type from the URL.
    match = _BOT_PATH_RE.match(request.url.path)
    if match is None:
        return None
    bot_id = match.group("bot_id")
    channel = match.group("channel_type")
    if not bot_id or not channel:
        return None

    tenant_raw = getattr(request.state, "record_tenant_id", None)
    if tenant_raw is None:
        return None
    tenant = str(tenant_raw) if isinstance(tenant_raw, UUID) else str(tenant_raw)

    # workspace_id is optional on the wire — resolver falls back to tenant
    # string per CLAUDE.md identity rule. Header > body > fallback.
    workspace = (
        request.headers.get("X-Workspace-Id")
        or getattr(request.state, "workspace_id", None)
        or tenant
    )

    return (tenant, str(workspace), bot_id, channel)


def _make_redis_key(identity: tuple[str, str, str, str], window_bucket: int) -> str:
    tenant, workspace, bot_id, channel = identity
    return f"{_BOT_RL_PREFIX}{tenant}:{workspace}:{bot_id}:{channel}:{window_bucket}"


class BotRateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-4-key bot rate limiter.

    Two policies:

    - ``ingest_per_min``: tighter cap (default 30/min) for routes
      matching :data:`_INGEST_HINT_RE`. Ingest kicks worker pipelines so
      a tenant flooding upload is throttled tighter than chat.
    - ``per_min``: default cap (120/min) for chat / general routes.
    """

    def __init__(
        self,
        app: object,
        *,
        per_min: int = DEFAULT_RL_BOT_PER_MIN,
        ingest_per_min: int = DEFAULT_RL_UPLOAD_PER_MIN,
        window_s: int = DEFAULT_RL_WINDOW_S,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._per_min = int(per_min)
        self._ingest_per_min = int(ingest_per_min)
        self._window_s = int(window_s)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        identity = _resolve_bot_identity(request)
        if identity is None:
            return await call_next(request)

        # Localhost-only loadtest bypass — operator-issued token short-
        # circuits the per-bot cap without disabling auth or UA denylist.
        if is_loadtest_bypass(request):
            return await call_next(request)

        redis_client = self._resolve_redis(request)
        if redis_client is None:
            return await call_next(request)

        is_ingest = _is_ingest_path(request.url.path)
        limit = self._ingest_per_min if is_ingest else self._per_min
        bucket = int(time.time() // self._window_s)
        key = _make_redis_key(identity, bucket)

        try:
            count = int(await redis_client.incr(key))
            if count == 1:
                # Pad the expiry slightly so a request landing at the tail
                # of a bucket cannot see a counter that already expired.
                await redis_client.expire(key, self._window_s + 5)
        except (RedisError, OSError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "bot_rate_limit_redis_failed",
                error_type=type(exc).__name__,
                err=str(exc)[:200],
            )
            return await call_next(request)

        reset_s = self._window_s - int(time.time() % self._window_s)
        headers = {
            "X-RateLimit-Bot-Limit": str(limit),
            "X-RateLimit-Bot-Window": str(self._window_s),
            "X-RateLimit-Bot-Remaining": str(max(0, limit - count)),
            "X-RateLimit-Bot-Reset": str(reset_s),
        }

        if count > limit:
            logger.warning(
                "bot_rate_limit_exceeded",
                record_tenant_id=identity[0],
                workspace_id=identity[1],
                bot_id=identity[2],
                channel_type=identity[3],
                count=count,
                limit=limit,
                is_ingest=is_ingest,
                path=request.url.path,
            )
            headers["Retry-After"] = str(reset_s)
            return JSONResponse(
                {
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": "BOT_RATE_LIMIT_EXCEEDED",
                        "message": (
                            f"bot {identity[2]} on channel {identity[3]} "
                            f"exceeded {limit} req/{self._window_s}s"
                        ),
                        "details": {
                            "record_tenant_id": identity[0],
                            "workspace_id": identity[1],
                            "bot_id": identity[2],
                            "channel_type": identity[3],
                            "limit": limit,
                            "window_s": self._window_s,
                            "tier": "ingest" if is_ingest else "chat",
                        },
                    },
                },
                status_code=429,
                headers=headers,
            )

        response = await call_next(request)
        for k, v in headers.items():
            response.headers[k] = v
        return response

    @staticmethod
    def _resolve_redis(request: Request) -> Any | None:
        """Pull the shared Redis client off the DI container.

        Returns ``None`` when the container is unwired (test env / pre-
        lifespan startup) or when the ``redis_client`` provider raises;
        the dispatch caller degrades open.
        """
        try:
            container = request.app.state.container
        except AttributeError:
            return None
        if container is None:
            return None
        provider = getattr(container, "redis_client", None)
        if provider is None:
            return None
        try:
            return provider()
        except (KeyError, TypeError, RuntimeError):
            return None


__all__ = ["BotRateLimitMiddleware"]
