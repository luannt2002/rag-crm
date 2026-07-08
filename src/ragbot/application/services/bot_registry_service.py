"""Bot registry cache — Redis-backed.

Cache key ``ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}``
→ ``BotConfig`` JSON. Bootstrap on FastAPI lifespan. Invalidate on admin
CRUD bot.

The 4-key identity ``(record_tenant_id UUID, workspace_id, bot_id,
channel_type)`` is mandatory — dropping any segment would either leak
across tenants or collapse two distinct workspaces' bots onto one cache
slot.

All writes set a TTL (``DEFAULT_BOT_CONFIG_TTL_S``); a missed
invalidation self-heals within the window instead of serving stale data.

Single-flight on cache miss: when N concurrent requests miss the same
identity, only one fires the DB query; the rest wait on an in-process
``asyncio.Lock`` (per identity tuple) and re-read the cache after the
writer back-fills it. Wired through ``AsyncSingleFlight`` (label
``bot_registry``) so the
``ragbot_cache_stampede_avoided_total{cache="bot_registry"}`` metric
tracks coalesced waiters.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.ports.repository_ports import BotRepositoryPort
from ragbot.shared.constants import DEFAULT_BOT_CONFIG_TTL_S
from ragbot.shared.single_flight import AsyncSingleFlight

logger = structlog.get_logger(__name__)

REDIS_PREFIX = "ragbot:bot"
BOT_LIST_KEY = "ragbot:bot_keys"
_SINGLE_FLIGHT_LABEL = "bot_registry"


class BotRegistryService:
    """Load + cache + lookup bot config by ``(record_tenant_id, workspace_id, bot_id, channel_type)``."""

    def __init__(
        self,
        repo: BotRepositoryPort,
        redis_client: Redis,
        system_repo: BotRepositoryPort | None = None,
    ) -> None:
        self._repo = repo
        # Cross-tenant warm (``bootstrap_cache`` loads EVERY tenant's active bots)
        # is a legitimate admin read that must NOT be tenant-scoped. Under the
        # NOBYPASSRLS app role it would fail-closed to 0 rows (no request tenant
        # bound at boot) → empty cache. ``system_repo`` (BYPASSRLS system factory)
        # keeps the warm working; per-tenant ``lookup`` stays on the app repo.
        # Defaults to ``repo`` so behaviour is unchanged when unwired (superuser
        # today: both factories are the same connection → no-op).
        self._system_repo = system_repo or repo
        self._redis = redis_client
        self._lock = asyncio.Lock()
        self._last_bootstrap_at: datetime | None = None
        self._last_reload_ts: float = 0.0
        self._reload_debounce_sec: float = 1.0
        # Per-identity single-flight; coalesces concurrent cache misses
        # for the same bot into one DB query.
        self._single_flight = AsyncSingleFlight(_SINGLE_FLIGHT_LABEL)

    @staticmethod
    def _key(
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> str:
        # Cache key carries the workspace slug so identical bot_ids in
        # distinct workspaces don't share state.
        return (
            f"{REDIS_PREFIX}:{record_tenant_id!s}"
            f":{workspace_id.strip()}"
            f":{bot_id.strip()}:{channel_type.strip()}"
        )

    async def bootstrap_cache(self) -> int:
        async with self._lock:
            # Cross-tenant warm via the system (BYPASSRLS) repo — see __init__.
            rows = await self._system_repo.list_active(record_tenant_id=None)

            old_keys = await self._redis.smembers(BOT_LIST_KEY)
            if old_keys:
                await self._redis.delete(*old_keys)
            await self._redis.delete(BOT_LIST_KEY)

            pipe = self._redis.pipeline()
            for c in rows:
                # Iterate bot rows and write each under its actual workspace
                # slug from the DB; the column is NOT NULL so this is safe.
                key = self._key(
                    c.record_tenant_id,
                    c.workspace_id,
                    c.bot_id,
                    c.channel_type,
                )
                pipe.set(key, c.model_dump_json(), ex=DEFAULT_BOT_CONFIG_TTL_S)
                pipe.sadd(BOT_LIST_KEY, key)
            await pipe.execute()

            self._last_bootstrap_at = datetime.now(tz=timezone.utc)
            logger.info("bot_registry_bootstrap", count=len(rows))
            return len(rows)

    async def lookup(
        self,
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> BotConfig | None:
        """Lookup bot config by ``(record_tenant_id, workspace_id, bot_id, channel_type)``.

        Reads Redis first; on miss reads DB and back-fills the cache. A cached
        row whose tenant doesn't match the request tenant is treated as poisoned
        and evicted.

        Stampede protection: when N concurrent callers race for the same
        identity on a cold cache, the first acquires the in-process
        ``AsyncSingleFlight`` lock and runs the DB query; the rest wait
        on the lock then re-check Redis (the writer is expected to have
        back-filled the cache by then). If the wait times out the waiter
        falls back to running its own DB query — better a duplicate
        query than a hung request.
        """
        if not workspace_id or not bot_id or not channel_type:
            return None
        workspace_id = workspace_id.strip()
        bot_id = bot_id.strip()
        channel_type = channel_type.strip()
        if not workspace_id or not bot_id or not channel_type:
            return None

        key = self._key(record_tenant_id, workspace_id, bot_id, channel_type)
        cached = await self._read_cache(key, record_tenant_id)
        if cached is not None:
            return cached

        # Single-flight: only one coroutine queries the DB for this
        # identity tuple. Fast path takes the lock + queries; slow path
        # waits + re-checks; timeout falls back to an independent fetch
        # (better duplicate than hung).
        sf_lock = await self._single_flight.get_lock(key)
        if not sf_lock.locked():
            async with sf_lock:
                # Double-check inside the lock — another waiter might have
                # written between our cache miss and lock acquire.
                cached = await self._read_cache(key, record_tenant_id)
                if cached is not None:
                    return cached
                return await self._fetch_and_cache(
                    key, record_tenant_id, workspace_id, bot_id, channel_type,
                )

        # Slow path — writer in flight.
        acquired = await self._single_flight.wait_for_lock_release(sf_lock)
        if not acquired:
            # Timeout — fall through to an independent fetch.
            return await self._fetch_and_cache(
                key, record_tenant_id, workspace_id, bot_id, channel_type,
            )
        try:
            cached = await self._read_cache(key, record_tenant_id)
            if cached is not None:
                return cached
            return await self._fetch_and_cache(
                key, record_tenant_id, workspace_id, bot_id, channel_type,
            )
        finally:
            sf_lock.release()

    async def _read_cache(
        self,
        key: str,
        record_tenant_id: UUID,
    ) -> BotConfig | None:
        """Read + tenant-validate the Redis cache entry.

        Returns ``None`` on miss or on a poisoned cross-tenant row (which
        is also evicted as a side effect — defensive).
        """
        raw = await self._redis.get(key)
        if raw is None:
            return None
        cfg = BotConfig.model_validate_json(raw)
        if cfg.record_tenant_id != record_tenant_id:
            logger.warning(
                "bot_registry_tenant_mismatch",
                key=key,
                expected_record_tenant_id=str(record_tenant_id),
                cached_record_tenant_id=str(cfg.record_tenant_id),
            )
            await self._redis.delete(key)
            await self._redis.srem(BOT_LIST_KEY, key)
            return None
        return cfg

    async def _fetch_and_cache(
        self,
        key: str,
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> BotConfig | None:
        """DB fallback + Redis back-fill — split out for single-flight use."""
        logger.info(
            "bot_registry_cache_miss",
            record_tenant_id=str(record_tenant_id),
            workspace_id=workspace_id,
            bot_id=bot_id,
            channel_type=channel_type,
        )
        cfg = await self._repo.find_by_4key(
            record_tenant_id, workspace_id, bot_id, channel_type,
        )
        if cfg is None:
            return None

        # CLAUDE.md Async Rule 1 + Rule 5 — two independent cache writes.
        # ``return_exceptions=True`` so a single Redis op failure doesn't
        # half-write the registry; failures are logged but don't break
        # the resolve path (Redis acts as a cache, not a SoR).
        results = await asyncio.gather(
            self._redis.set(
                key, cfg.model_dump_json(), ex=DEFAULT_BOT_CONFIG_TTL_S,
            ),
            self._redis.sadd(BOT_LIST_KEY, key),
            return_exceptions=True,
        )
        for op_name, result in zip(("set", "sadd"), results):
            if isinstance(result, BaseException):
                logger.warning(
                    "bot_registry_cache_write_failed",
                    op=op_name,
                    key=key,
                    error=str(result),
                )
        return cfg

    async def invalidate(
        self,
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> None:
        """Re-load (or remove) cache entry for one bot."""
        async with self._lock:
            key = self._key(record_tenant_id, workspace_id, bot_id, channel_type)
            cfg = await self._repo.find_by_4key(
                record_tenant_id, workspace_id, bot_id, channel_type,
            )
            if cfg is None:
                # CLAUDE.md Async Rule 1 + Rule 5 — DEL + SREM are
                # independent cache invalidations; gather to halve
                # latency. Errors are tolerated (cache; not SoR).
                results = await asyncio.gather(
                    self._redis.delete(key),
                    self._redis.srem(BOT_LIST_KEY, key),
                    return_exceptions=True,
                )
                for op_name, result in zip(("delete", "srem"), results):
                    if isinstance(result, BaseException):
                        logger.warning(
                            "bot_registry_cache_invalidate_failed",
                            op=op_name,
                            key=key,
                            error=str(result),
                        )
                logger.info(
                    "bot_registry_removed",
                    record_tenant_id=str(record_tenant_id),
                    workspace_id=workspace_id,
                    bot_id=bot_id,
                    channel_type=channel_type,
                )
            else:
                # CLAUDE.md Async Rule 1 + Rule 5 — SET + SADD are
                # independent cache writes; mirror _fetch_and_cache pattern.
                results = await asyncio.gather(
                    self._redis.set(
                        key, cfg.model_dump_json(), ex=DEFAULT_BOT_CONFIG_TTL_S,
                    ),
                    self._redis.sadd(BOT_LIST_KEY, key),
                    return_exceptions=True,
                )
                for op_name, result in zip(("set", "sadd"), results):
                    if isinstance(result, BaseException):
                        logger.warning(
                            "bot_registry_cache_write_failed",
                            op=op_name,
                            key=key,
                            error=str(result),
                        )
                logger.info(
                    "bot_registry_reloaded",
                    record_tenant_id=str(record_tenant_id),
                    workspace_id=workspace_id,
                    bot_id=bot_id,
                    channel_type=channel_type,
                )

    async def invalidate_all(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_reload_ts
        if elapsed < self._reload_debounce_sec:
            return
        self._last_reload_ts = now
        await self.bootstrap_cache()

    async def cache_status(self) -> dict[str, Any]:
        keys = await self._redis.smembers(BOT_LIST_KEY)
        return {
            "size": len(keys),
            "keys_sample": [k.decode() if isinstance(k, bytes) else k for k in list(keys)[:10]],
            "last_bootstrap_at": (
                self._last_bootstrap_at.isoformat()
                if self._last_bootstrap_at else None
            ),
        }


__all__ = ["BotRegistryService"]
