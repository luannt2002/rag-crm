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
"""FailoverOrchestrator — central resolver for per-resource circuit breakers.

Phase D Stream D1 (GA-Hardening). One orchestrator instance per process
holds the breaker cache for every external resource. Callers ask for a
breaker by resource key (``redis``, ``db``, ``llm``); the orchestrator
returns the same instance on every call so state is preserved across
requests.

Feature flag (default ENABLED — defensive):

* ``circuit_breaker_enabled = True``  → real adapter from
  ``infrastructure/resilience/registry.py``.
* ``circuit_breaker_enabled = False`` → every ``get`` returns a
  ``NullCircuitBreaker`` (always closed, never raises).

The flag is read once at construction. Hot-reload = build a new
orchestrator via ``bootstrap.py`` (mirrors the rest of the DI container).

Per-provider LLM breakers
--------------------------

The LLM resource supports a ``provider_code`` suffix so each upstream
(``openai``, ``anthropic``, ``cohere``, …) gets a distinct breaker.
This is the same pattern the dynamic LiteLLM router already uses
locally — surfacing it through the orchestrator unifies the cache and
keeps state machine ownership in one place.
"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

import structlog

from ragbot.application.services.retry_policy import CircuitBreakerPolicy
from ragbot.infrastructure.resilience.null_circuit_breaker import (
    NullCircuitBreaker,
)
from ragbot.infrastructure.resilience.registry import build_circuit_breaker
from ragbot.shared.constants import (
    CB_RESOURCE_LLM,
    DEFAULT_CIRCUIT_BREAKER_ENABLED,
)

if TYPE_CHECKING:
    from ragbot.application.ports.circuit_breaker_port import CircuitBreakerPort

logger = structlog.get_logger(__name__)


class FailoverOrchestrator:
    """Central per-resource circuit-breaker registry + feature flag gate."""

    def __init__(
        self,
        *,
        enabled: bool = DEFAULT_CIRCUIT_BREAKER_ENABLED,
        policy: CircuitBreakerPolicy | None = None,
    ) -> None:
        """Build the orchestrator.

        @param enabled: feature flag. ``False`` ⇒ every ``get`` returns
            a ``NullCircuitBreaker``. Default ``True`` (defensive).
        @param policy: shared policy applied to every newly-created
            breaker. ``None`` uses module defaults.
        """
        self._enabled = enabled
        self._policy = policy
        self._cache: dict[str, CircuitBreakerPort] = {}
        self._lock = RLock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _cache_key(self, resource: str, suffix: str | None) -> str:
        # Suffix-aware key so LLM breakers fan out per provider.
        return resource if not suffix else f"{resource}:{suffix}"

    def get(
        self,
        resource: str,
        *,
        provider_code: str | None = None,
    ) -> CircuitBreakerPort:
        """Return the (cached) breaker for ``resource``.

        @param resource: registered key (``redis`` / ``db`` / ``llm``).
            Unknown ⇒ ``NullCircuitBreaker``.
        @param provider_code: only meaningful for the LLM resource —
            distinct codes produce distinct cached breakers so each
            upstream provider has its own state machine.
        """
        suffix = provider_code if resource == CB_RESOURCE_LLM else None
        cache_key = self._cache_key(resource, suffix)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            if not self._enabled:
                breaker: CircuitBreakerPort = NullCircuitBreaker(name=cache_key)
            else:
                build_kwargs: dict[str, object] = {}
                if self._policy is not None:
                    build_kwargs["policy"] = self._policy
                if resource == CB_RESOURCE_LLM and provider_code:
                    build_kwargs["provider_code"] = provider_code
                breaker = build_circuit_breaker(resource, **build_kwargs)
            self._cache[cache_key] = breaker
            logger.info(
                "circuit_breaker_resolved",
                resource=resource,
                provider_code=provider_code,
                cache_key=cache_key,
                enabled=self._enabled,
                breaker=type(breaker).__name__,
            )
            return breaker

    def reset_all(self) -> None:
        """Force-CLOSE every cached breaker (admin / test hook)."""
        with self._lock:
            for breaker in self._cache.values():
                breaker.reset()

    def snapshot(self) -> dict[str, str]:
        """Return ``{cache_key: state_name}`` for observability."""
        with self._lock:
            return {k: b.state.value for k, b in self._cache.items()}


__all__ = ["FailoverOrchestrator"]
