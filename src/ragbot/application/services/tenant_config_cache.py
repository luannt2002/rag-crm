"""P33 — Tenant config cache (bypass_rate_limit + rate_limit_per_min + monthly_token_cap).

Loaded once per request boundary in middleware. Cached in Redis with TTL
``DEFAULT_TENANT_CONFIG_TTL_S`` so cross-request reads avoid hitting the
DB on every chat call. The fields are tiny + mutate slowly (admin-flip
bypass, ops-tune limit) so a short TTL is fine — at worst an admin flip
takes one TTL to take effect.

Redis key shape::

    ragbot:tenant_cfg:{record_tenant_id}

UUID-keyed — looks up ``tenants.id`` (PK) directly. Stored as JSON dict
with nullable int / bool fields. Misses fall back to the SQLAlchemy
session_factory and reload the row before caching.

Why not extend BotRegistryService? Two reasons:
1. ``bots`` table is per-bot; a tenant has N bots. The 3 fields above are
   strictly tenant-scoped.
2. BotRegistry bootstrap walks every active bot — we don't want to fan
   out tenant-config writes from there. Decoupling keeps ownership
   clean.

Single-flight on cache miss: N concurrent first-requests for the same
tenant hit one ``_load_from_db`` call instead of N parallel queries.
Wired through ``AsyncSingleFlight`` (label ``tenant_config``) so the
``ragbot_cache_stampede_avoided_total{cache="tenant_config"}`` metric
tracks coalesced waiters.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ragbot.infrastructure.db.models import TenantModel
from ragbot.shared.constants import DEFAULT_TENANT_CONFIG_TTL_S
from ragbot.shared.single_flight import AsyncSingleFlight

logger = structlog.get_logger(__name__)

REDIS_PREFIX = "ragbot:tenant_cfg"
_SINGLE_FLIGHT_LABEL = "tenant_config"


@dataclass(slots=True, frozen=True)
class TenantRuntimeConfig:
    """Tiny DTO — only the fields the runtime layer needs each request.

    ``allowed_origins`` carries the per-tenant CORS strict whitelist
    consulted by ``CORSPerTenantMiddleware``. Empty tuple = block all
    browser cross-origin traffic for this tenant.
    """

    bypass_rate_limit: bool
    rate_limit_per_min: int | None  # None → inherit system_config / fallback
    monthly_token_cap: int | None  # None → no cap
    allowed_origins: tuple[str, ...] = ()  # CORS strict whitelist


class TenantConfigCache:
    """Redis-backed cache of the per-tenant runtime config row.

    Lookup precedence: Redis cache → DB row → ``None`` (caller treats as
    safe-default = no bypass, no override).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[Any],
        redis_client: Any,
        *,
        ttl_s: int = DEFAULT_TENANT_CONFIG_TTL_S,
    ) -> None:
        self._sf = session_factory
        self._redis = redis_client
        self._ttl_s = int(ttl_s)
        # Coalesce concurrent cache misses for the same tenant.
        self._single_flight = AsyncSingleFlight(_SINGLE_FLIGHT_LABEL)

    @staticmethod
    def _key(record_tenant_id: UUID) -> str:
        return f"{REDIS_PREFIX}:{record_tenant_id!s}"

    async def get(self, record_tenant_id: UUID) -> TenantRuntimeConfig | None:
        """Return the cached row for ``record_tenant_id`` or load from DB.

        Never raises — Redis blip falls through to DB, DB blip returns
        ``None`` and the caller picks the safe default. ``record_tenant_id``
        is the UUID PK of ``tenants.id``.

        Single-flight: when N concurrent callers race on a cold cache for
        the same tenant, only one fires ``_load_from_db``; the rest wait
        on the per-tenant ``asyncio.Lock`` and re-read Redis after the
        writer back-fills the cache. Timeout falls back to an independent
        fetch (better duplicate than hung).
        """
        key = self._key(record_tenant_id)

        cached = await self._read_cache_json(record_tenant_id, key)
        if cached is not None:
            return cached

        # Single-flight: only one DB query per (tenant) on cold cache.
        sf_lock = await self._single_flight.get_lock(key)
        if not sf_lock.locked():
            async with sf_lock:
                cached = await self._read_cache_json(record_tenant_id, key)
                if cached is not None:
                    return cached
                return await self._load_and_cache(record_tenant_id)

        # Slow path — writer in flight.
        acquired = await self._single_flight.wait_for_lock_release(sf_lock)
        if not acquired:
            return await self._load_and_cache(record_tenant_id)
        try:
            cached = await self._read_cache_json(record_tenant_id, key)
            if cached is not None:
                return cached
            return await self._load_and_cache(record_tenant_id)
        finally:
            sf_lock.release()

    async def _read_cache_json(
        self, record_tenant_id: UUID, key: str,
    ) -> TenantRuntimeConfig | None:
        """Decode the Redis JSON payload — returns ``None`` on miss / blip."""
        try:
            raw = await self._redis.get(key)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.debug(
                "tenant_cfg_cache_redis_get_error",
                err=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            origins_raw = data.get("allowed_origins") or ()
            return TenantRuntimeConfig(
                bypass_rate_limit=bool(data.get("bypass_rate_limit", False)),
                rate_limit_per_min=(
                    int(data["rate_limit_per_min"])
                    if data.get("rate_limit_per_min") is not None
                    else None
                ),
                monthly_token_cap=(
                    int(data["monthly_token_cap"])
                    if data.get("monthly_token_cap") is not None
                    else None
                ),
                allowed_origins=tuple(str(o) for o in origins_raw),
            )
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "tenant_cfg_cache_decode_error",
                record_tenant_id=str(record_tenant_id), err=str(exc),
            )
            return None

    async def _load_and_cache(
        self, record_tenant_id: UUID,
    ) -> TenantRuntimeConfig | None:
        """DB fallback + Redis back-fill — split out for single-flight use."""
        cfg = await self._load_from_db(record_tenant_id)
        if cfg is not None:
            await self._write_cache(record_tenant_id, cfg)
        return cfg

    async def invalidate(self, record_tenant_id: UUID) -> None:
        """Drop cached row — call after admin updates the row."""
        try:
            await self._redis.delete(self._key(record_tenant_id))
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.debug(
                "tenant_cfg_cache_invalidate_error",
                err=str(exc),
                error_type=type(exc).__name__,
            )

    async def _load_from_db(
        self, record_tenant_id: UUID,
    ) -> TenantRuntimeConfig | None:
        """Resolve UUID → tenant policy row by PK lookup.

        Returns ``None`` when the tenant does not exist or the DB is down;
        caller falls back to safe defaults.
        """
        try:
            async with self._sf() as session:
                stmt = select(
                    TenantModel.bypass_rate_limit,
                    TenantModel.rate_limit_per_min,
                    TenantModel.monthly_token_cap,
                    TenantModel.allowed_origins,
                ).where(TenantModel.id == record_tenant_id)
                row = (await session.execute(stmt)).first()
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            logger.warning(
                "tenant_cfg_cache_db_error",
                record_tenant_id=str(record_tenant_id),
                err=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if row is None:
            return None
        bypass, per_min, cap, origins_raw = row
        if origins_raw is None:
            origins_tuple: tuple[str, ...] = ()
        else:
            origins_tuple = tuple(str(o) for o in (origins_raw or ()))
        return TenantRuntimeConfig(
            bypass_rate_limit=bool(bypass),
            rate_limit_per_min=(int(per_min) if per_min is not None else None),
            monthly_token_cap=(int(cap) if cap is not None else None),
            allowed_origins=origins_tuple,
        )

    async def _write_cache(
        self, record_tenant_id: UUID, cfg: TenantRuntimeConfig,
    ) -> None:
        try:
            payload = json.dumps(
                {
                    "bypass_rate_limit": cfg.bypass_rate_limit,
                    "rate_limit_per_min": cfg.rate_limit_per_min,
                    "monthly_token_cap": cfg.monthly_token_cap,
                    "allowed_origins": list(cfg.allowed_origins),
                },
            )
            await self._redis.set(
                self._key(record_tenant_id), payload, ex=self._ttl_s,
            )
        except (RedisError, OSError, asyncio.TimeoutError, TypeError) as exc:
            logger.debug(
                "tenant_cfg_cache_write_error",
                err=str(exc),
                error_type=type(exc).__name__,
            )


__all__ = ["TenantConfigCache", "TenantRuntimeConfig"]
