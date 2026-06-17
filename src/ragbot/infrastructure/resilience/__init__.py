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
