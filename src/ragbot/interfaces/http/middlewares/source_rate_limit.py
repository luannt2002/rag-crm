"""Per-source-tag rate limit middleware (Phase 5 — 2026-05-18).

Sits OUTSIDE :class:`BotRateLimitMiddleware` (composes in front so the
4-key bot RL stays the inner gate). Scope key is the pair
``(record_tenant_id, source_tag)`` so a single tenant running two
upstream BE services — say ``kms-a`` and ``kms-b`` — gets a
**per-source-tag bucket** rather than one shared tenant counter. KMS-A
flooding ingest cannot starve KMS-B.

Scope
-----
This layer fires ONLY on requests whose path starts with the
configured ``path_prefix``. The default resolves at install time to
``app.api_base_path`` + :data:`SOURCE_RL_INGEST_PATH_SUFFIX` — the
unified documents ingest endpoint. All other paths bypass — the
per-IP + per-token (sliding) layer ahead caps general traffic, and the
4-key bot RL behind caps per-bot ingest.

Bypass conditions
-----------------
- Path does NOT match the prefix → bypass.
- ``X-Source-Tag`` header missing / empty after strip → bypass (per-IP
  + per-token layers handle anonymous-source traffic).
- ``request.state.record_tenant_id`` absent (pre-auth path) → bypass.
- DI container / Redis client not wired (test env) → bypass.
- Redis INCR / EXPIRE raises a known transport error → degrade open
  (log ``source_rate_limit_redis_failed`` + pass through).

Application-MINDSET
-------------------
Pure transport back-pressure. NO body / template / refusal text
injected. 429 JSON body documents cap dimensions only.

Headers on success
------------------
- ``X-RateLimit-Source-Remaining`` — requests left in this window.

On 429:

- ``X-RateLimit-Source-Limit`` — configured per-window cap.
- ``X-RateLimit-Source-Window`` — window size in seconds.
- ``X-RateLimit-Source-Remaining`` — always ``0`` here.
- ``Retry-After`` — seconds until counter resets.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.shared.constants import (
    DEFAULT_SOURCE_RL_PER_MIN,
    DEFAULT_SOURCE_RL_WINDOW_S,
    SOURCE_RL_INGEST_PATH_SUFFIX,
    SOURCE_RL_TAG_MAX_LEN,
)

logger = structlog.get_logger(__name__)

_SOURCE_RL_PREFIX = "ragbot:rl:source:"
_SOURCE_TAG_HEADER = "X-Source-Tag"


def _resolve_source_tag(request: Request) -> str | None:
    """Pull ``X-Source-Tag`` header, strip whitespace, truncate at
    :data:`SOURCE_RL_TAG_MAX_LEN`.

    Returns ``None`` when the header is missing or empty after strip —
    caller must bypass.
    """
    raw = request.headers.get(_SOURCE_TAG_HEADER)
    if raw is None:
        return None
    tag = raw.strip()
    if not tag:
        return None
    if len(tag) > SOURCE_RL_TAG_MAX_LEN:
        tag = tag[:SOURCE_RL_TAG_MAX_LEN]
    return tag


def _resolve_tenant(request: Request) -> str | None:
    """Extract ``record_tenant_id`` from request state (set by
    :class:`TenantContextMiddleware`).

    Returns ``None`` for pre-auth paths — caller must bypass.
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return str(raw)
    return str(raw)


def _make_redis_key(tenant: str, source_tag: str, window_bucket: int) -> str:
    """Compose the Redis bucket key — 2-key isolation: tenant + source_tag.

    Two tenants both naming a source ``kms-a`` get different keys; one
    tenant running ``kms-a`` and ``kms-b`` also gets different keys.
    """
    return f"{_SOURCE_RL_PREFIX}{tenant}:{source_tag}:{window_bucket}"


class SourceRateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-source-tag rate limiter for ingest traffic.

    Args:
        app: ASGI app.
        per_min: per-window request cap (default
            :data:`DEFAULT_SOURCE_RL_PER_MIN`).
        window_s: window size in seconds (default
            :data:`DEFAULT_SOURCE_RL_WINDOW_S`).
        path_prefix: only requests starting with this prefix enter the
            gate; everything else bypasses. When ``None`` (the default)
            the prefix resolves at init time to
            ``app.api_base_path`` + :data:`SOURCE_RL_INGEST_PATH_SUFFIX`.
    """

    def __init__(
        self,
        app: object,
        *,
        per_min: int = DEFAULT_SOURCE_RL_PER_MIN,
        window_s: int = DEFAULT_SOURCE_RL_WINDOW_S,
        path_prefix: str | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._per_min = int(per_min)
        self._window_s = int(window_s)
        if path_prefix is None:
            # Local import — keeps the middleware module load-light and
            # avoids a settings import at the module level where some
            # test fixtures override the settings before the app boots.
            from ragbot.config.settings import get_settings  # noqa: PLC0415
            path_prefix = (
                f"{get_settings().app.api_base_path}"
                f"{SOURCE_RL_INGEST_PATH_SUFFIX}"
            )
        self._path_prefix = str(path_prefix)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # 1. Path scope gate — only the unified ingest endpoint.
        if not request.url.path.startswith(self._path_prefix):
            return await call_next(request)

        # 2. Header gate — opaque BE-service identifier.
        source_tag = _resolve_source_tag(request)
        if source_tag is None:
            return await call_next(request)

        # 3. Tenant gate — pre-auth never reaches the source layer.
        tenant = _resolve_tenant(request)
        if tenant is None:
            return await call_next(request)

        # 4. DI gate — Redis client off the lifespan-bound container.
        redis_client = self._resolve_redis(request)
        if redis_client is None:
            return await call_next(request)

        bucket = int(time.time() // self._window_s)
        key = _make_redis_key(tenant, source_tag, bucket)

        # 5. INCR + EXPIRE — fixed-window counter.
        try:
            count = int(await redis_client.incr(key))
            if count == 1:
                # Pad the expiry slightly so a request landing at the
                # tail of a bucket cannot see a counter that already
                # expired.
                await redis_client.expire(key, self._window_s + 5)
        except (RedisError, OSError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "source_rate_limit_redis_failed",
                error_type=type(exc).__name__,
                err=str(exc)[:200],
                record_tenant_id=tenant,
                source_tag=source_tag,
            )
            return await call_next(request)

        reset_s = self._window_s - int(time.time() % self._window_s)
        limit = self._per_min

        # 6. Cap exceeded → 429.
        if count > limit:
            logger.warning(
                "source_rate_limit_exceeded",
                record_tenant_id=tenant,
                source_tag=source_tag,
                count=count,
                limit=limit,
                path=request.url.path,
            )
            return JSONResponse(
                {
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": "SOURCE_RATE_LIMIT_EXCEEDED",
                        "message": (
                            f"source {source_tag} exceeded "
                            f"{limit} req/{self._window_s}s for this tenant"
                        ),
                        "details": {
                            "record_tenant_id": tenant,
                            "source_tag": source_tag,
                            "limit": limit,
                            "window_s": self._window_s,
                        },
                    },
                },
                status_code=429,
                headers={
                    "X-RateLimit-Source-Limit": str(limit),
                    "X-RateLimit-Source-Window": str(self._window_s),
                    "X-RateLimit-Source-Remaining": "0",
                    "Retry-After": str(reset_s),
                },
            )

        # 7. Under cap → add remaining header on success.
        response = await call_next(request)
        response.headers["X-RateLimit-Source-Remaining"] = str(
            max(0, limit - count),
        )
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


__all__ = ["SourceRateLimitMiddleware"]
