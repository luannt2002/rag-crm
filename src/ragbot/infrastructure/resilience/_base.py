"""Shared adapter base for per-resource circuit-breaker strategies.

Each concrete resource adapter (Redis / DB / LLM API) wraps the same
``CircuitBreaker`` state machine but exposes a stable name + per-resource
policy default. Keeping the shared code here means every adapter file
is just policy + constructor wiring, satisfying the "one provider per
file" Strategy convention without duplicating the delegation methods.
"""

from __future__ import annotations

from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
    CircuitBreakerPolicy,
)


class _ResourceBreakerAdapter:
    """Implements ``CircuitBreakerPort`` by delegating to ``CircuitBreaker``.

    Subclasses set ``resource_key`` (e.g. ``redis``) so the public ``name``
    matches the registry key used by callers / metrics.
    """

    resource_key: str = ""

    def __init__(
        self,
        *,
        policy: CircuitBreakerPolicy | None = None,
        name_suffix: str | None = None,
    ) -> None:
        """Create the adapter.

        @param policy: per-resource policy override. ``None`` uses module
            defaults (5 fails, 30 s base cooldown, 15 s step, 120 s cap).
        @param name_suffix: optional discriminator appended after the
            resource key (e.g. provider code for ``llm`` so each upstream
            LLM provider has its own breaker). Final name format:
            ``{resource_key}`` or ``{resource_key}:{name_suffix}``.
        """
        if not self.resource_key:
            msg = "_ResourceBreakerAdapter subclass must set resource_key"
            raise ValueError(msg)
        breaker_name = (
            self.resource_key
            if not name_suffix
            else f"{self.resource_key}:{name_suffix}"
        )
        self._breaker = CircuitBreaker(
            name=breaker_name,
            policy=policy or CircuitBreakerPolicy(),
        )

    # ------------------------------------------------------------------
    # CircuitBreakerPort surface.
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self._breaker.name

    @property
    def state(self) -> CBState:
        return self._breaker.state

    def can_execute(self) -> bool:
        return self._breaker.can_execute()

    def record_success(self) -> None:
        self._breaker.record_success()

    def record_failure(self) -> None:
        self._breaker.record_failure()

    def reset(self) -> None:
        """Force-CLOSE — clears fail-count, last-failure, consec-open, window.

        Routes through the breaker's explicit ``reset()``: ``record_success`` is
        a no-op while OPEN (only a HALF_OPEN probe may close), so a reset via
        record_success would silently leave a stuck-OPEN breaker open.
        """
        self._breaker.reset()


__all__ = ["_ResourceBreakerAdapter"]
