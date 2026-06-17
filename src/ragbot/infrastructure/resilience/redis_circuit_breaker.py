"""Redis circuit-breaker strategy.

Wraps Redis client calls. When OPEN, callers fast-fail and the cache
layer falls through to DB direct (graceful degradation per Phase D D1
spec) instead of waiting on a hung TCP socket.
"""

from __future__ import annotations

from ragbot.application.services.retry_policy import CircuitBreakerPolicy
from ragbot.infrastructure.resilience._base import _ResourceBreakerAdapter
from ragbot.shared.constants import CB_RESOURCE_REDIS


class RedisCircuitBreaker(_ResourceBreakerAdapter):
    """``CircuitBreakerPort`` adapter for Redis."""

    resource_key = CB_RESOURCE_REDIS

    def __init__(self, *, policy: CircuitBreakerPolicy | None = None) -> None:
        super().__init__(policy=policy)


__all__ = ["RedisCircuitBreaker"]
