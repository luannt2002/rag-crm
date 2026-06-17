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
