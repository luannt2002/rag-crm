# ============================================================
# DEAD-CODE NOTICE
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * import-graph scan over src/ (FastAPI app + workers +
#     middlewares + routes): zero non-test importers
#   * the live circuit breakers used in production are the
#     per-adapter breakers inside the embedder / reranker
#     infrastructure adapters, NOT this registry/orchestrator
#
# Reason: this resilience registry + FailoverOrchestrator was
# never wired in bootstrap.py or the graph. Only the unit test
# tests/unit/resilience/test_failover_orchestrator.py exercises it.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Tests kept INTACT
#   * Do NOT delete; defer physical removal to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings + dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================
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
