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
