"""PostgreSQL circuit-breaker strategy.

Wraps DB engine calls. When the primary DB is slow (>statement_timeout)
or returns connection errors, sustained failures trip OPEN and callers
fast-fail rather than queueing on an exhausted pool.
"""

from __future__ import annotations

from ragbot.application.services.retry_policy import CircuitBreakerPolicy
from ragbot.infrastructure.resilience._base import _ResourceBreakerAdapter
from ragbot.shared.constants import CB_RESOURCE_DB


class DbCircuitBreaker(_ResourceBreakerAdapter):
    """``CircuitBreakerPort`` adapter for PostgreSQL / SQLAlchemy."""

    resource_key = CB_RESOURCE_DB

    def __init__(self, *, policy: CircuitBreakerPolicy | None = None) -> None:
        super().__init__(policy=policy)


__all__ = ["DbCircuitBreaker"]
