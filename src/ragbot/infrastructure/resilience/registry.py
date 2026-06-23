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
"""Circuit-breaker strategy registry — DI factory keyed by resource string.

Pattern mirrors ``infrastructure/retrieval_fallback/registry.py``:

* ``_REGISTRY`` maps resource key → strategy class.
* ``build_circuit_breaker(resource, **kwargs)`` constructs the matching
  adapter, falling back to ``NullCircuitBreaker`` on unknown / empty keys.
* ``list_resources()`` returns the registered keys (sorted).

Adding a new resource = new file in this package + one line in
``_REGISTRY`` (Open-Closed). Orchestration code stays untouched.

Unknown / typo resource names degrade silently to ``NullCircuitBreaker``
(always-closed pass-through) so a misconfig in ``system_config`` cannot
crash boot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ragbot.infrastructure.resilience.db_circuit_breaker import DbCircuitBreaker
from ragbot.infrastructure.resilience.llm_circuit_breaker import LlmCircuitBreaker
from ragbot.infrastructure.resilience.null_circuit_breaker import (
    NullCircuitBreaker,
)
from ragbot.infrastructure.resilience.redis_circuit_breaker import (
    RedisCircuitBreaker,
)
from ragbot.shared.constants import (
    CB_RESOURCE_DB,
    CB_RESOURCE_LLM,
    CB_RESOURCE_REDIS,
)

if TYPE_CHECKING:
    from ragbot.application.ports.circuit_breaker_port import CircuitBreakerPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    CB_RESOURCE_REDIS: RedisCircuitBreaker,
    CB_RESOURCE_DB: DbCircuitBreaker,
    CB_RESOURCE_LLM: LlmCircuitBreaker,
    "null": NullCircuitBreaker,
}


def build_circuit_breaker(
    resource: str | None = None,
    **kwargs: Any,
) -> "CircuitBreakerPort":
    """Construct the circuit-breaker strategy matching ``resource``.

    @param resource: registry key. ``None`` / unknown / empty falls back
        to ``NullCircuitBreaker`` and emits a warning so the misconfig is
        observable.
    @param kwargs: forwarded to the strategy constructor (e.g.
        ``policy=...`` or ``provider_code=...`` for the LLM adapter).
    """
    key = (resource or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "circuit_breaker_unknown_resource_fallback_null",
            requested=resource,
            registered=sorted(_REGISTRY.keys()),
        )
        # Preserve the requested name on the Null fallback so logs /
        # metrics can identify which misconfigured key landed here.
        return NullCircuitBreaker(name=key)
    try:
        return cls(**kwargs)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        logger.error(
            "circuit_breaker_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullCircuitBreaker(name=key)


def list_resources() -> list[str]:
    """Return registered resource names sorted (stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_circuit_breaker", "list_resources"]
