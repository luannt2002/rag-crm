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
"""Null circuit-breaker — always-CLOSED pass-through.

Used when ``circuit_breaker_enabled = false`` in ``system_config`` (the
feature-flag OFF state) or when the registry receives an unknown
resource key. Never raises; ``can_execute()`` is always ``True``;
``record_*`` are no-ops.
"""

from __future__ import annotations

from ragbot.application.services.retry_policy import CBState


class NullCircuitBreaker:
    """No-op ``CircuitBreakerPort`` implementation."""

    def __init__(self, *, name: str = "null") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CBState:
        return CBState.CLOSED

    def can_execute(self) -> bool:
        return True

    def record_success(self) -> None:
        return None

    def record_failure(self) -> None:
        return None

    def reset(self) -> None:
        return None


__all__ = ["NullCircuitBreaker"]
