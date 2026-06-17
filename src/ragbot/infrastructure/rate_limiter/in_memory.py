"""In-memory sliding-window rate limiter — for tests and single-process dev.

Same contract as :class:`SlidingWindowRateLimiter`, but state lives in a
process-local dict. NOT safe for multi-worker deployments — used by the
unit / integration tests so they need not spin up Redis.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Deque

from ragbot.application.ports.rate_limiter_port import (
    RateLimiterDecision,
    RateLimiterPort,
)


class InMemorySlidingWindow(RateLimiterPort):
    """Process-local sliding-window limiter.

    Each key carries an asyncio.Lock so concurrent ``check`` calls on
    the same key see a serialised view of the deque. Cross-key access
    is fully concurrent.
    """

    def __init__(self) -> None:
        self._steady: dict[str, Deque[float]] = defaultdict(deque)
        self._burst: dict[str, Deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

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
        async with self._locks[key]:
            now_ms = time.time() * 1000.0
            window_ms = max(int(window_s), 1) * 1000.0
            cutoff = now_ms - window_ms
            steady = self._steady[key]
            while steady and steady[0] < cutoff:
                steady.popleft()
            used = len(steady)

            burst_active = burst_factor > 1.0 and burst_window_s > 0
            burst_used = 0
            burst_limit = 0
            if burst_active:
                burst = self._burst[key]
                burst_cutoff = now_ms - (int(burst_window_s) * 1000.0)
                while burst and burst[0] < burst_cutoff:
                    burst.popleft()
                burst_used = len(burst)
                burst_limit = int(limit * burst_factor)

            effective_ceiling = limit
            source = "sliding"
            if burst_active and burst_used < burst_limit:
                effective_ceiling = burst_limit
                source = "burst"

            if used >= effective_ceiling:
                oldest = steady[0] if steady else now_ms
                free_at = oldest + window_ms
                retry_s = max(int((free_at - now_ms) / 1000.0 + 0.999), 1)
                return RateLimiterDecision(
                    allowed=False,
                    limit=int(limit),
                    remaining=0,
                    reset_unix=int((now_ms + retry_s * 1000.0) // 1000),
                    retry_after_s=retry_s,
                    source=source,
                    used=used,
                )

            steady.append(now_ms)
            if burst_active:
                self._burst[key].append(now_ms)
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

    def reset(self) -> None:
        """Drop all state — convenience for between-test cleanup."""
        self._steady.clear()
        self._burst.clear()
        self._locks.clear()


__all__ = ("InMemorySlidingWindow",)
