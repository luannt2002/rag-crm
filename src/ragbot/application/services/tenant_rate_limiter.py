"""Per-tenant rate limiter (Layer 1, before per-user Layer 2).

Two independent bypass channels OR-merged at evaluation time:

* ``tenants.bypass_rate_limit=TRUE`` skips ALL bots of the tenant
  (platform-admin control; rare — VIP / internal partners).
* ``bots.bypass_rate_limit=TRUE`` skips a single bot row (tenant-owner
  control).

Resolution chain for the effective limit (when neither bypass fires)::

    tenants.rate_limit_per_min
      → system_config.tenant_rate_limit_per_min
        → DEFAULT_TENANT_RATE_LIMIT_PER_MIN

A resolved value of 0 means "soft-unlimited" — the counter is still
maintained but no 429 is returned. NULL never reaches the runtime; it
collapses into the next level of the chain.

Redis key shape::

    rl:tenant:{record_tenant_id}:{minute_bucket}

UUID-keyed; ``minute_bucket = floor(now / window_s)``. INCR + EXPIRE on
first hit gives a fixed-window counter. Cross-tenant keys never collide
because the UUID is part of the prefix.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError

from ragbot.application.ports.metrics_port import MetricsPort
from ragbot.shared.constants import (
    DEFAULT_TENANT_RATE_LIMIT_PER_MIN,
    DEFAULT_TENANT_RATE_LIMIT_WINDOW_S,
)

logger = structlog.get_logger(__name__)

# Redis key prefix — tenant-scoped, distinct from per-user (`ragbot:rl:`)
# and per-service-token paths so the counters never share buckets.
_TENANT_RL_PREFIX = "rl:tenant:"


@dataclass(slots=True, frozen=True)
class TenantRateLimitDecision:
    """Outcome of a single Layer-1 check.

    ``allowed=True`` means the request may proceed. ``bypass=True`` means
    the counter was not even consulted (saves a Redis round-trip when the
    caller wants to skip all downstream checks too).
    """

    allowed: bool
    bypass: bool
    source: str  # "tenant_bypass" | "bot_bypass" | "tenant" | "system" | "fallback"
    limit: int
    window_s: int
    used: int  # post-INCR counter (0 when bypass)


class TenantRateLimiter:
    """Layer-1 per-tenant rate limiter backed by Redis fixed-window counter."""

    def __init__(
        self,
        redis_client: Any,
        *,
        default_limit: int = DEFAULT_TENANT_RATE_LIMIT_PER_MIN,
        default_window_s: int = DEFAULT_TENANT_RATE_LIMIT_WINDOW_S,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._redis = redis_client
        self._default_limit = int(default_limit)
        self._default_window_s = int(default_window_s)
        self._metrics = metrics

    @staticmethod
    def resolve_effective(
        *,
        tenant_bypass: bool | None,
        bot_bypass: bool | None,
        tenant_limit: int | None,
        system_limit: int | None,
        default_limit: int = DEFAULT_TENANT_RATE_LIMIT_PER_MIN,
    ) -> tuple[int, str, bool]:
        """Pure resolver — returns ``(limit, source, bypass)``.

        ``limit=0`` semantics: soft-unlimited (counter not enforced).
        ``bypass=True`` short-circuits before any counter touch.

        Two bypass flags are independent — OR-merged here, NEVER merged
        in the schema (different actors / blast radius).
        """
        if tenant_bypass:
            return (0, "tenant_bypass", True)
        if bot_bypass:
            return (0, "bot_bypass", True)
        if tenant_limit is not None:
            return (int(tenant_limit), "tenant", False)
        if system_limit is not None:
            return (int(system_limit), "system", False)
        return (int(default_limit), "fallback", False)

    async def _incr_counter(
        self,
        record_tenant_id: UUID,
        *,
        window_s: int,
    ) -> int | None:
        """INCR + EXPIRE the per-tenant fixed-window counter.

        Returns the post-INCR count, or ``None`` on Redis backend error
        (caller decides fail-open vs fail-closed — Layer-1 fails open).
        Narrow except — only Redis-shaped errors swallow; programming
        errors propagate.
        """
        now = int(time.time())
        bucket = now // window_s
        key = f"{_TENANT_RL_PREFIX}{record_tenant_id!s}:{bucket}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                # +1 grace second so the bucket clears after the window
                # rather than mid-window.
                await self._redis.expire(key, window_s + 1)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "tenant_rate_limit_redis_error",
                record_tenant_id=str(record_tenant_id),
                err=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        return int(count)

    async def check(
        self,
        record_tenant_id: UUID,
        *,
        tenant_bypass: bool | None = False,
        bot_bypass: bool | None = False,
        tenant_limit: int | None = None,
        system_limit: int | None = None,
    ) -> TenantRateLimitDecision:
        """Check + increment the counter for ``record_tenant_id``.

        Returns ``allowed=False`` only when the resolved limit > 0 AND
        the post-INCR count exceeds it.

        **Bypass is observability-preserving**: when ``tenant_bypass`` or
        ``bot_bypass`` is set the request is always ``allowed`` and the
        decision carries ``bypass=True`` (so the caller can also
        short-circuit any Layer-2 per-user check), but the underlying
        Redis counter is **still INCR'd** so dashboards keep visibility
        of VIP / internal-partner traffic.

        Redis errors fail-open (caller may layer fail-closed on top —
        the per-user middleware already does this).
        """
        limit, source, bypass = self.resolve_effective(
            tenant_bypass=tenant_bypass,
            bot_bypass=bot_bypass,
            tenant_limit=tenant_limit,
            system_limit=system_limit,
            default_limit=self._default_limit,
        )
        window_s = self._default_window_s

        if bypass:
            # Count bypass traffic so VIP / internal-partner usage stays
            # visible on the admin dashboard.
            count = await self._incr_counter(
                record_tenant_id, window_s=window_s,
            )
            if self._metrics is not None:
                # MetricsPort impls swallow prometheus errors internally.
                self._metrics.inc_rate_limit_bypass(
                    tenant_id=str(record_tenant_id), source=source,
                )
            return TenantRateLimitDecision(
                allowed=True, bypass=True, source=source,
                limit=0, window_s=window_s,
                used=int(count) if count is not None else 0,
            )

        if limit <= 0:
            # Soft-unlimited — explicit 0 from tenant or system override.
            # Counter intentionally not touched (different actor / audit
            # dimension from bypass; preserves prior semantics).
            return TenantRateLimitDecision(
                allowed=True, bypass=False, source=source,
                limit=0, window_s=window_s, used=0,
            )

        count = await self._incr_counter(record_tenant_id, window_s=window_s)
        if count is None:
            # Redis unavailable — fail-open at Layer-1.
            return TenantRateLimitDecision(
                allowed=True, bypass=False, source=source,
                limit=limit, window_s=window_s, used=0,
            )
        return TenantRateLimitDecision(
            allowed=count <= limit,
            bypass=False,
            source=source,
            limit=limit,
            window_s=window_s,
            used=int(count),
        )


async def check_tenant_rate_limit(
    redis_client: Any,
    record_tenant_id: UUID,
    *,
    tenant_bypass: bool | None = False,
    bot_bypass: bool | None = False,
    tenant_limit: int | None = None,
    system_limit: int | None = None,
) -> bool:
    """Convenience function — returns True if the request is allowed.

    Mirrors the plan's ``check_tenant_rate_limit(record_tenant_id) -> bool``
    signature for callers that don't need the full decision object.
    """
    limiter = TenantRateLimiter(redis_client)
    decision = await limiter.check(
        record_tenant_id,
        tenant_bypass=tenant_bypass,
        bot_bypass=bot_bypass,
        tenant_limit=tenant_limit,
        system_limit=system_limit,
    )
    return decision.allowed


__all__ = [
    "TenantRateLimitDecision",
    "TenantRateLimiter",
    "check_tenant_rate_limit",
]
