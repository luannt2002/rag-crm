"""Redis-backed sliding-window rate limiter (Layer-2).

Algorithm
---------
For each ``key`` we maintain a Redis SORTED SET with score = millisecond
timestamp of each accepted request. On every check:

1. ZREMRANGEBYSCORE key 0 (now_ms - window_ms) — drop expired entries.
2. ZCARD key — count remaining entries (= used in the current window).
3. If used < limit (or burst-allowed): ZADD key now_ms now_ms; EXPIRE
   key window_s + 1; return ``allowed=True``.
4. Else: return ``allowed=False`` with ``retry_after_s`` derived from
   the oldest entry (``ZRANGE key 0 0 WITHSCORES``).

Burst allowance
---------------
When ``burst_factor > 1.0`` and ``burst_window_s > 0`` we maintain a
SECOND sorted set keyed ``{key}:burst`` covering only the last
``burst_window_s`` seconds. The effective ceiling for the head of the
window is ``floor(limit * burst_factor)``; once burst is exhausted the
steady-state ``limit`` re-applies for the rest of the window. The
``source`` field on the decision distinguishes ``"burst"`` (first
``burst_window_s`` cool-down) from ``"sliding"`` (steady state).

Failure mode
------------
Redis errors fail-OPEN (return ``allowed=True``, ``source="fail_open"``).
The middleware wrapping this limiter may layer fail-closed on top — see
``DEFAULT_RL_FAIL_MODE``.

Thread / coroutine safety
-------------------------
Each operation is a single Redis pipeline (atomic on the server side).
No client-side lock needed. Cross-process consistency is exact; clock
skew between client + Redis affects window edges by ≤1s in practice.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from redis.exceptions import RedisError

from ragbot.application.ports.rate_limiter_port import (
    RateLimiterDecision,
    RateLimiterPort,
)

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "rl:slide:"
_BURST_SUFFIX = ":burst"


class SlidingWindowRateLimiter(RateLimiterPort):
    """Redis-backed implementation of :class:`RateLimiterPort`."""

    def __init__(self, redis_client: Any, *, key_prefix: str = _KEY_PREFIX) -> None:
        self._redis = redis_client
        self._prefix = key_prefix

    async def check(
        self,
        key: str,
        *,
        limit: int,
        window_s: int,
        burst_factor: float = 1.0,
        burst_window_s: int = 0,
    ) -> RateLimiterDecision:
        if limit <= 0:
            # Soft-unlimited — never enforced. Return a stable decision so
            # callers can still echo headers without branching.
            now = int(time.time())
            return RateLimiterDecision(
                allowed=True,
                limit=0,
                remaining=0,
                reset_unix=now + max(int(window_s), 1),
                retry_after_s=0,
                source="unlimited",
                used=0,
            )

        now_ms = int(time.time() * 1000)
        window_ms = max(int(window_s), 1) * 1000
        cutoff_ms = now_ms - window_ms
        steady_key = f"{self._prefix}{key}"

        try:
            # Atomic pipeline: prune expired + count + (later) add.
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(steady_key, 0, cutoff_ms)
            pipe.zcard(steady_key)
            results = await pipe.execute()
            used = int(results[1] or 0)

            burst_used = 0
            burst_limit = 0
            burst_active = burst_factor > 1.0 and burst_window_s > 0
            if burst_active:
                burst_key = f"{steady_key}{_BURST_SUFFIX}"
                burst_window_ms = int(burst_window_s) * 1000
                burst_cutoff_ms = now_ms - burst_window_ms
                burst_pipe = self._redis.pipeline()
                burst_pipe.zremrangebyscore(burst_key, 0, burst_cutoff_ms)
                burst_pipe.zcard(burst_key)
                burst_results = await burst_pipe.execute()
                burst_used = int(burst_results[1] or 0)
                burst_limit = int(limit * burst_factor)

            # Decide effective ceiling for THIS request.
            effective_ceiling = limit
            source = "sliding"
            if burst_active and burst_used < burst_limit:
                # Inside the burst sub-window — allow up to burst_limit
                # requests in the steady window before throttling. The
                # steady counter still ticks; this just opens the door
                # for the head of the window.
                effective_ceiling = burst_limit
                source = "burst"

            if used >= effective_ceiling:
                # Reject — derive retry_after from oldest entry.
                oldest = await self._redis.zrange(
                    steady_key, 0, 0, withscores=True,
                )
                retry_s = self._compute_retry_after(
                    oldest=oldest, now_ms=now_ms, window_ms=window_ms,
                )
                return RateLimiterDecision(
                    allowed=False,
                    limit=int(limit),
                    remaining=0,
                    reset_unix=int((now_ms + retry_s * 1000) // 1000),
                    retry_after_s=max(retry_s, 1),
                    source=source,
                    used=used,
                )

            # Accept — record into steady (and burst) sets.
            accept_pipe = self._redis.pipeline()
            accept_pipe.zadd(steady_key, {str(now_ms): now_ms})
            accept_pipe.expire(steady_key, max(int(window_s), 1) + 1)
            if burst_active:
                burst_key = f"{steady_key}{_BURST_SUFFIX}"
                accept_pipe.zadd(burst_key, {str(now_ms): now_ms})
                accept_pipe.expire(burst_key, max(int(burst_window_s), 1) + 1)
            await accept_pipe.execute()

            new_used = used + 1
            remaining = max(0, int(limit) - new_used)
            reset_unix = int((now_ms + window_ms) // 1000)
            return RateLimiterDecision(
                allowed=True,
                limit=int(limit),
                remaining=remaining,
                reset_unix=reset_unix,
                retry_after_s=0,
                source=source,
                used=new_used,
            )
        except (RedisError, OSError, asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning(
                "sliding_rate_limit_redis_error",
                key=key,
                err=str(exc),
                error_type=type(exc).__name__,
            )
            now = int(time.time())
            return RateLimiterDecision(
                allowed=True,  # fail-open at this layer; caller may fail-closed.
                limit=int(limit),
                remaining=int(limit),
                reset_unix=now + max(int(window_s), 1),
                retry_after_s=0,
                source="fail_open",
                used=0,
            )

    @staticmethod
    def _compute_retry_after(
        *,
        oldest: list[tuple[bytes, float]] | list[Any],
        now_ms: int,
        window_ms: int,
    ) -> int:
        """Derive the retry-after seconds from the oldest entry's score."""
        if not oldest:
            return max(int(window_ms // 1000), 1)
        try:
            oldest_score = float(oldest[0][1])
        except (IndexError, TypeError, ValueError):
            return max(int(window_ms // 1000), 1)
        # The window will free a slot when the oldest entry expires.
        free_at_ms = int(oldest_score) + window_ms
        delta_ms = max(free_at_ms - now_ms, 1000)
        return int((delta_ms + 999) // 1000)


__all__ = ("SlidingWindowRateLimiter",)
