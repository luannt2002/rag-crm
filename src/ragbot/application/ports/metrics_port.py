"""MetricsPort — Protocol for application-layer telemetry emission.

Minimal facade over the prometheus_client metric singletons so
application services (e.g. ``StepTracker``, ``TenantRateLimiter``) can
emit metrics without importing
``ragbot.infrastructure.observability.metrics`` directly.

Two methods are enough to cover the current application-layer call sites
(see Issue #7 in the deep-dive report). New counters / histograms should
gain a method here before a service starts using them — keeps the
boundary thin and makes the Null implementation trivial for unit tests.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsPort(Protocol):
    """Telemetry emission contract — MUST be non-raising.

    Implementations swallow prometheus client errors at debug level;
    a broken meter MUST NOT crash request handling.
    """

    def observe_step_duration(self, step_name: str, seconds: float) -> None:
        """Record one observation on the per-step latency histogram."""
        ...

    def inc_rate_limit_bypass(self, *, tenant_id: str, source: str) -> None:
        """Increment the per-tenant rate-limit-bypass counter."""
        ...


__all__ = ["MetricsPort"]
