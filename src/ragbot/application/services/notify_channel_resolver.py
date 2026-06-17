"""Notify channel resolver — DB row → env fallback → None.

The dispatcher calls ``resolve()`` before every POST to pick up admin
edits without a redeploy. A short Redis TTL (default 60s) keeps the
common case off DB while still letting an admin's PATCH propagate
quickly. ``invalidate()`` is wired into the admin write path so the
flip is immediate, the TTL only matters for the dormant cache after
process restart.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import structlog
from pydantic import ValidationError

from ragbot.application.dto.notify_channel import NotifyChannelConfig
from ragbot.shared.constants import (
    DEFAULT_NOTIFY_CHANNEL_CACHE_TTL_S,
    NOTIFY_CHANNEL_CONFIG_KEY,
)

logger = structlog.get_logger(__name__)

# Process-wide cache key — the channel is platform-level, not per-tenant,
# so a single key is correct (avoids fan-out on bust).
_CACHE_KEY = "ragbot:notify_channel:config"

# Sentinel persisted to Redis when the resolver wants to remember "no
# config" without re-querying DB on every miss. Using a string value
# keeps the cache codec simple; the resolver matches it explicitly.
_CACHE_NONE_SENTINEL = "__none__"

ResolutionSource = Literal["db", "env", "none"]


class NotifyChannelResolver:
    """Resolve the active notify channel with DB-first lookup.

    Order:

    1. Redis cache hit (``ragbot:notify_channel:config``).
    2. ``system_config`` row keyed by ``NOTIFY_CHANNEL_CONFIG_KEY`` —
       JSON dict matching ``NotifyChannelConfig``.
    3. ``settings.notify_channel_config`` — boot-time env fallback.
    4. ``None`` when nothing is configured (dispatcher drops + counts
       ``notify_dropped_total{reason="unconfigured"}``).
    """

    def __init__(
        self,
        system_config_service,
        redis_client,
        env_settings,
    ) -> None:
        self._scs = system_config_service
        self._redis = redis_client
        self._env_settings = env_settings

    async def resolve(self) -> tuple[NotifyChannelConfig | None, ResolutionSource]:
        """Return ``(config, source)`` — source ∈ {"db","env","none"}.

        Validation errors at the DB layer fall through to env so an
        admin can recover by editing the env-side default; the bad row
        is logged for triage.
        """
        cached = await self._read_cache()
        if cached is not None:
            return cached

        # 1) DB row.
        db_value = await self._scs.get(NOTIFY_CHANNEL_CONFIG_KEY)
        if isinstance(db_value, dict):
            try:
                cfg = NotifyChannelConfig.model_validate(db_value)
            except ValidationError as exc:
                logger.warning(
                    "notify_channel_db_row_invalid",
                    error_type=type(exc).__name__,
                    err=str(exc),
                )
            else:
                await self._write_cache(cfg)
                return cfg, "db"

        # 2) Env fallback.
        env_value = getattr(self._env_settings, "notify_channel_config", None)
        if isinstance(env_value, dict):
            try:
                cfg = NotifyChannelConfig.model_validate(env_value)
            except ValidationError as exc:
                logger.warning(
                    "notify_channel_env_invalid",
                    error_type=type(exc).__name__,
                    err=str(exc),
                )
            else:
                await self._write_cache(cfg, source="env")
                return cfg, "env"

        # 3) Nothing configured — remember the negative result briefly
        # so the dispatcher does not query DB on every miss.
        await self._write_none_sentinel()
        return None, "none"

    async def invalidate(self) -> None:
        """Drop the Redis cache so the next ``resolve`` re-reads DB."""
        try:
            await self._redis.delete(_CACHE_KEY)
        except (OSError, ConnectionError, TimeoutError) as exc:
            # Cache bust failure is best-effort; the TTL will naturally
            # expire and the admin write itself succeeded.
            logger.warning(
                "notify_channel_cache_invalidate_failed",
                error_type=type(exc).__name__,
                err=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _read_cache(
        self,
    ) -> tuple[NotifyChannelConfig | None, ResolutionSource] | None:
        try:
            raw = await self._redis.get(_CACHE_KEY)
        except (OSError, ConnectionError, TimeoutError) as exc:
            logger.warning(
                "notify_channel_cache_read_failed",
                error_type=type(exc).__name__,
                err=str(exc),
            )
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if raw == _CACHE_NONE_SENTINEL:
            return None, "none"
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        cfg_dict = payload.get("config")
        source_raw = payload.get("source")
        if not isinstance(cfg_dict, dict) or source_raw not in ("db", "env"):
            return None
        try:
            cfg = NotifyChannelConfig.model_validate(cfg_dict)
        except ValidationError:
            return None
        return cfg, source_raw  # type: ignore[return-value]

    async def _write_cache(
        self,
        cfg: NotifyChannelConfig,
        *,
        source: ResolutionSource = "db",
    ) -> None:
        payload = {
            "source": source,
            "config": cfg.model_dump(mode="json"),
        }
        try:
            await self._redis.set(
                _CACHE_KEY,
                json.dumps(payload),
                ex=DEFAULT_NOTIFY_CHANNEL_CACHE_TTL_S,
            )
        except (OSError, ConnectionError, TimeoutError) as exc:
            logger.warning(
                "notify_channel_cache_write_failed",
                error_type=type(exc).__name__,
                err=str(exc),
            )

    async def _write_none_sentinel(self) -> None:
        try:
            await self._redis.set(
                _CACHE_KEY,
                _CACHE_NONE_SENTINEL,
                ex=DEFAULT_NOTIFY_CHANNEL_CACHE_TTL_S,
            )
        except (OSError, ConnectionError, TimeoutError):
            # Negative-cache write failure is harmless — next call falls
            # through to DB / env again.
            pass


__all__ = ["NotifyChannelResolver", "ResolutionSource"]
