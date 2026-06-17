"""Circuit-breaker Port — Protocol contract for per-resource breakers.

Phase D Stream D1 (GA-Hardening). Wraps the ``CircuitBreaker`` state
machine (``application/services/retry_policy.py``) behind a stable port
so each *resource* (Redis, DB, LLM API provider, …) can pick an adapter
without orchestration code depending on the concrete class.

State semantics (mirror the underlying CB):

* ``CLOSED`` — calls pass through.
* ``OPEN`` — calls fast-fail with ``CircuitBreakerOpen``; cooldown is
  adaptive (base + step × consecutive opens, capped at max).
* ``HALF_OPEN`` — one probe allowed per cooldown elapse; success ⇒
  ``CLOSED``, failure ⇒ ``OPEN`` again.

The port stays minimal — adapters can hold extra state (e.g. per-key
buckets for Redis cluster nodes) but the public surface is the same six
methods every caller relies on.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ragbot.application.services.retry_policy import CBState


@runtime_checkable
class CircuitBreakerPort(Protocol):
    """Per-resource circuit-breaker contract.

    Implementations: ``NullCircuitBreaker`` (always closed, default-off),
    ``RedisCircuitBreaker``, ``DbCircuitBreaker``, ``LlmCircuitBreaker``.
    """

    @property
    def name(self) -> str:
        """Stable identifier for metrics / logs (e.g. ``redis``, ``db``,
        ``llm:openai``)."""
        ...

    @property
    def state(self) -> CBState:
        """Current state machine value."""
        ...

    def can_execute(self) -> bool:
        """``True`` when the caller may issue the wrapped call.

        Side-effect: transitions ``OPEN`` ⇒ ``HALF_OPEN`` once the
        cooldown has elapsed.
        """
        ...

    def record_success(self) -> None:
        """Inform the breaker that the wrapped call succeeded."""
        ...

    def record_failure(self) -> None:
        """Inform the breaker that the wrapped call failed."""
        ...

    def reset(self) -> None:
        """Force-CLOSE the breaker (admin / test hook)."""
        ...


__all__ = ["CircuitBreakerPort"]
