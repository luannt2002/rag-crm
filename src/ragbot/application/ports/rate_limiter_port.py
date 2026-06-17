"""Rate limiter Port — contract for sliding-window strategies.

Open-Closed: callers depend on this Protocol; implementations
(``SlidingWindowRateLimiter`` Redis-backed, ``InMemorySlidingWindow``
in-process for tests, future Token-Bucket variant) plug in via the
registry at ``infrastructure/rate_limiter/registry.py``.

Why a separate Port from the existing :class:`TenantRateLimiter`?
=================================================================
The existing ``TenantRateLimiter`` is a coarse Layer-1 per-tenant fixed
window (resolves bypass + caps). The new Port targets Layer-2: per-token
+ per-endpoint sliding window with burst allowance and W3C-style
response headers. Different scope, different abstraction.

Decision contract
-----------------
``RateLimiterDecision`` is a frozen dataclass — never mutated post-check.
The middleware reads ``allowed`` to short-circuit, and ``limit`` /
``remaining`` / ``reset_unix`` / ``retry_after`` to populate response
headers. ``source`` is observability metadata (``"sliding"`` / ``"burst"``
/ ``"unlimited"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class RateLimiterDecision:
    """Outcome of a single sliding-window check.

    Fields map 1-to-1 to the W3C ``RateLimit-*`` header draft so the
    middleware needs no further computation.
    """

    allowed: bool
    limit: int  # Steady-state limit (excludes burst headroom).
    remaining: int  # max(0, limit - used) at the moment of the check.
    reset_unix: int  # Unix timestamp when the current window rolls.
    retry_after_s: int  # Seconds the caller should back off (0 when allowed).
    source: str  # "sliding" | "burst" | "unlimited" | "fail_open"
    used: int  # Post-INCR counter (steady-state window).


@runtime_checkable
class RateLimiterPort(Protocol):
    """Sliding-window rate limiter contract.

    Implementations must be coroutine-safe for concurrent calls on the
    same key (Redis ZADD/ZREMRANGEBYSCORE pipeline gives this
    naturally; in-memory impl uses an asyncio.Lock per key).
    """

    async def check(
        self,
        key: str,
        *,
        limit: int,
        window_s: int,
        burst_factor: float = 1.0,
        burst_window_s: int = 0,
    ) -> RateLimiterDecision:
        """Check and atomically consume one slot for ``key``.

        Args:
            key: Caller identifier (token jti, IP, composite).
            limit: Steady-state requests permitted per ``window_s``.
                ``0`` = soft-unlimited (counter not enforced; decision
                is always ``allowed=True`` with ``source="unlimited"``).
            window_s: Steady-state window length in seconds.
            burst_factor: Multiplier applied to ``limit`` for the
                ``burst_window_s`` head of the window. ``1.0`` disables
                burst (steady-state only). ``2.0`` means the first
                ``burst_window_s`` seconds may consume ``2 × limit``
                requests before the steady-state limit kicks in.
            burst_window_s: Burst sub-window length. ``0`` disables.

        Returns:
            :class:`RateLimiterDecision` — never raises (implementations
            log + fail-open on backend error; caller layers
            fail-closed if needed).
        """
        ...


__all__ = ("RateLimiterDecision", "RateLimiterPort")
