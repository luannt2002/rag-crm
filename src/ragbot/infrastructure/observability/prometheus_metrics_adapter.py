"""Prometheus adapter implementing ``MetricsPort``.

Thin wrapper over the module-level metric singletons in
``ragbot.infrastructure.observability.metrics``. Swallows prometheus
client errors at debug level — a broken meter MUST NOT crash request
handling.
"""

from __future__ import annotations

import structlog

from ragbot.application.ports.metrics_port import MetricsPort
from ragbot.infrastructure.observability.metrics import (
    rate_limit_bypass_observed_total,
    step_duration_seconds,
)

logger = structlog.get_logger(__name__)


class PrometheusMetricsAdapter(MetricsPort):
    """Concrete ``MetricsPort`` backed by prometheus_client singletons."""

    def observe_step_duration(self, step_name: str, seconds: float) -> None:
        try:
            step_duration_seconds.labels(step_name=step_name).observe(seconds)
        except (ValueError, TypeError) as exc:
            logger.debug(
                "metrics_step_duration_skip",
                step_name=step_name,
                err=str(exc),
                error_type=type(exc).__name__,
            )

    def inc_rate_limit_bypass(self, *, tenant_id: str, source: str) -> None:
        try:
            rate_limit_bypass_observed_total.labels(
                tenant_id=tenant_id, source=source,
            ).inc()
        except (ValueError, TypeError) as exc:
            logger.debug(
                "metrics_rate_limit_bypass_skip",
                err=str(exc),
                error_type=type(exc).__name__,
            )


__all__ = ["PrometheusMetricsAdapter"]
