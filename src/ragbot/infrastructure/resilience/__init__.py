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
"""Resilience adapters — per-resource circuit-breaker Strategy implementations.

Phase D Stream D1 (GA-Hardening). Each external resource (Redis, DB,
LLM API providers) owns a ``CircuitBreakerPort`` adapter wired through
``FailoverOrchestrator``. Orchestration code resolves the breaker by
resource key — never imports the concrete class.

Adding a new resource = drop a new adapter file + one registry line; no
caller changes (Open-Closed).
"""

from __future__ import annotations

from ragbot.infrastructure.resilience.db_circuit_breaker import (
    DbCircuitBreaker,
)
from ragbot.infrastructure.resilience.failover_orchestrator import (
    FailoverOrchestrator,
)
from ragbot.infrastructure.resilience.llm_circuit_breaker import (
    LlmCircuitBreaker,
)
from ragbot.infrastructure.resilience.null_circuit_breaker import (
    NullCircuitBreaker,
)
from ragbot.infrastructure.resilience.redis_circuit_breaker import (
    RedisCircuitBreaker,
)
from ragbot.infrastructure.resilience.registry import (
    build_circuit_breaker,
    list_resources,
)

__all__ = [
    "DbCircuitBreaker",
    "FailoverOrchestrator",
    "LlmCircuitBreaker",
    "NullCircuitBreaker",
    "RedisCircuitBreaker",
    "build_circuit_breaker",
    "list_resources",
]
