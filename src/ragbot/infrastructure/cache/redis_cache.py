"""Redis CachePort implementation."""

from __future__ import annotations

import asyncio

import structlog
from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError

from ragbot.application.ports.cache_port import CachePort
from ragbot.shared.constants import (
    DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_S,
    DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT_S,
    DEFAULT_REDIS_SOCKET_TIMEOUT_S,
    DEFAULT_REDIS_STREAMS_MAX_CONNECTIONS,
    DEFAULT_REDIS_STREAMS_SOCKET_CONNECT_TIMEOUT_S,
    DEFAULT_REDIS_STREAMS_SOCKET_TIMEOUT_S,
)
from ragbot.shared.errors import CacheError

logger = structlog.get_logger(__name__)


def create_redis_client(url: str, *, max_connections: int = 50) -> Redis:
    """Create async Redis client for SHORT operations (cache, rate-limit, INCR).

    Tuned for sub-second hot-path use. ``XREADGROUP``-style **blocking
    reads** MUST NOT share this client — its ``socket_timeout`` is
    shorter than the ``block`` parameter and every read raises
    ``TimeoutError`` before the server responds. Use
    :func:`create_redis_streams_client` for the bus/streams path.

    Tunables (defaults from ``shared/constants.py`` SSoT — override via
    env vars if needed by operator):

    - socket_timeout: abort any single op after this long (slow Redis → don't
      hang coroutines indefinitely). Sub-second budget for cache/rate-limit.
    - socket_connect_timeout: reject connect attempts after this (dead host).
    - health_check_interval: ping idle conns to drop stale sockets after
      Redis restart. redis-py reconnects on next cmd.
    - retry_on_timeout: auto-retry 1 op on TimeoutError (cheap — slow net).
    - socket_keepalive: keep TCP conns alive across NAT / LB idle timeouts.
    """
    return from_url(
        url,
        max_connections=max_connections,
        decode_responses=False,
        socket_timeout=DEFAULT_REDIS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT_S,
        health_check_interval=DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_S,
        retry_on_timeout=True,
        socket_keepalive=True,
    )


def create_redis_streams_client(
    url: str,
    *,
    max_connections: int = DEFAULT_REDIS_STREAMS_MAX_CONNECTIONS,
) -> Redis:
    """Create async Redis client tuned for Redis Streams XREADGROUP.

    The bus uses ``XREADGROUP ... BLOCK 5_000`` to long-poll for events.
    Sharing the cache client (``socket_timeout=2.0``) causes every
    blocking read to raise :class:`asyncio.TimeoutError` BEFORE the
    server responds — the symptom observed in production
    (``redis_streams_read_error`` loop, doc upload stuck DRAFT).

    Tunables (defaults from ``shared/constants.py`` SSoT):

    - socket_timeout: 30s — must be > XREADGROUP block (5s) + headroom.
    - socket_connect_timeout: 5s — bus is slower to reconnect than
      cache (acceptable cold-path).
    - max_connections: 20 — bus opens one connection per subscribe loop
      plus a few for XACK / XCLAIM; 20 covers a single-process worker
      plus future fan-out.
    """
    return from_url(
        url,
        max_connections=max_connections,
        decode_responses=False,
        socket_timeout=DEFAULT_REDIS_STREAMS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=DEFAULT_REDIS_STREAMS_SOCKET_CONNECT_TIMEOUT_S,
        health_check_interval=DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_S,
        retry_on_timeout=True,
        socket_keepalive=True,
    )


class RedisCache(CachePort):
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def health_check(self) -> bool:
        try:
            return await self._client.ping()  # type: ignore[no-any-return]
        except (RedisError, OSError, asyncio.TimeoutError):
            return False

    async def get(self, key: str) -> bytes | None:
        try:
            return await self._client.get(key)  # type: ignore[no-any-return]
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            raise CacheError(f"redis get failed: {exc}") from exc

    async def set(self, key: str, value: bytes, *, ttl_s: int) -> None:
        try:
            await self._client.set(key, value, ex=ttl_s)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            raise CacheError(f"redis set failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def exists(self, key: str) -> bool:
        n = await self._client.exists(key)
        return bool(n)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except (RedisError, OSError, asyncio.TimeoutError):
            pass


__all__ = ["RedisCache", "create_redis_client", "create_redis_streams_client"]
