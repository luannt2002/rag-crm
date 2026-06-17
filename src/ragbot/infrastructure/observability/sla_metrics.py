"""SLA monitoring layer — threshold breach detection helpers.

Phase D Stream D3 deliverable. The chat-request latency histogram, error
counter, cache-hit counter, and circuit-breaker gauge already live in
``ragbot.infrastructure.observability.metrics``; this module adds a thin
*classification* layer that turns a sampled measurement into a coarse
``SLAStatus`` verdict (``OK`` / ``WARN`` / ``CRITICAL``).

Two consumers benefit:

1. **Prometheus alert rules** — see ``scripts/sla_alerting_rules.yaml``.
   The YAML referenes the same numeric thresholds exported here so YAML
   and Python never drift. ``sla_threshold_snapshot()`` returns the live
   threshold map for tests / debug endpoints.
2. **In-process health probes** — e.g. a future ``/health/sla`` route or
   a background reporter that flips Slack notifications. Both can call
   ``classify_latency`` / ``classify_error_rate`` directly without
   re-implementing the threshold comparison.

Thresholds are loaded from ``shared.constants`` (compile-time defaults).
Operators may override at runtime by passing a ``thresholds`` dict — the
caller is responsible for fetching the live values from
``SystemConfigService`` and forwarding them. This module is *config-aware
but not config-bound* so it stays unit-testable without Redis / DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

import structlog

from ragbot.shared.constants import (
    DEFAULT_SLA_BREACH_WINDOW_S,
    DEFAULT_SLA_CACHE_HIT_RATIO_WARN,
    DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S,
    DEFAULT_SLA_ERROR_RATE_CRITICAL,
    DEFAULT_SLA_ERROR_RATE_WARN,
    DEFAULT_SLA_P95_CRITICAL_SECONDS,
    DEFAULT_SLA_P95_WARN_SECONDS,
)

logger = structlog.get_logger(__name__)


class SLAStatus(str, Enum):
    """Coarse 3-level verdict shared by every SLA classifier in this module.

    Encoded as ``str`` so the value renders cleanly in structured logs,
    Prometheus annotations, and JSON HTTP probes without extra conversion.
    """

    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SLAThresholds:
    """Snapshot of the SLA threshold tuple.

    Frozen dataclass instead of a free-form dict so callers get a typed
    contract (mypy catches typos like ``thresholds.p95_critic``) and so
    the dataclass becomes hashable for cache keying.
    """

    p95_warn_seconds: float = DEFAULT_SLA_P95_WARN_SECONDS
    p95_critical_seconds: float = DEFAULT_SLA_P95_CRITICAL_SECONDS
    error_rate_warn: float = DEFAULT_SLA_ERROR_RATE_WARN
    error_rate_critical: float = DEFAULT_SLA_ERROR_RATE_CRITICAL
    cache_hit_ratio_warn: float = DEFAULT_SLA_CACHE_HIT_RATIO_WARN
    circuit_open_duration_s: float = DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S
    breach_window_s: float = DEFAULT_SLA_BREACH_WINDOW_S


# Module-level immutable default. Tests + alert-rule generators import this
# directly to avoid every caller having to know about ``shared.constants``.
DEFAULT_SLA_THRESHOLDS: Final[SLAThresholds] = SLAThresholds()


def classify_latency(
    p95_seconds: float,
    *,
    thresholds: SLAThresholds = DEFAULT_SLA_THRESHOLDS,
) -> SLAStatus:
    """Classify a sampled p95 latency reading against the SLA bands.

    Returns ``CRITICAL`` first (precedence rule — a critical breach is
    always reported as critical even if it also crosses the warn band).
    Negative durations are treated as ``OK`` — a corrupt sample MUST NOT
    page on-call.
    """
    if p95_seconds < 0:
        return SLAStatus.OK
    if p95_seconds >= thresholds.p95_critical_seconds:
        return SLAStatus.CRITICAL
    if p95_seconds >= thresholds.p95_warn_seconds:
        return SLAStatus.WARN
    return SLAStatus.OK


def classify_error_rate(
    error_rate: float,
    *,
    thresholds: SLAThresholds = DEFAULT_SLA_THRESHOLDS,
) -> SLAStatus:
    """Classify an HTTP 5xx / pipeline error ratio (0.0-1.0) against SLA.

    Out-of-range inputs are clamped: negatives → ``OK``; values above 1
    are evaluated as-is so a corrupt 1.2 still trips CRITICAL instead of
    being silently swallowed.
    """
    if error_rate < 0:
        return SLAStatus.OK
    if error_rate >= thresholds.error_rate_critical:
        return SLAStatus.CRITICAL
    if error_rate >= thresholds.error_rate_warn:
        return SLAStatus.WARN
    return SLAStatus.OK


def classify_cache_hit_ratio(
    hit_ratio: float,
    *,
    thresholds: SLAThresholds = DEFAULT_SLA_THRESHOLDS,
) -> SLAStatus:
    """Classify a cache hit ratio (0.0-1.0).

    *Low* ratio is the alert direction (the cache is supposed to absorb
    traffic; falling below ``cache_hit_ratio_warn`` means upstream cost
    is creeping). No CRITICAL band — cache miss is a perf / cost issue,
    never a user-visible outage.
    """
    if hit_ratio < 0 or hit_ratio > 1:
        return SLAStatus.OK
    if hit_ratio < thresholds.cache_hit_ratio_warn:
        return SLAStatus.WARN
    return SLAStatus.OK


def classify_circuit_open_duration(
    duration_open_s: float,
    *,
    thresholds: SLAThresholds = DEFAULT_SLA_THRESHOLDS,
) -> SLAStatus:
    """Classify how long a circuit breaker has been in OPEN state.

    A circuit that stays OPEN past ``circuit_open_duration_s`` indicates
    the downstream provider is *durably* failing and a human should
    intervene (rotate API keys, swap binding) — page CRITICAL.
    """
    if duration_open_s < 0:
        return SLAStatus.OK
    if duration_open_s >= thresholds.circuit_open_duration_s:
        return SLAStatus.CRITICAL
    return SLAStatus.OK


def sla_threshold_snapshot(
    thresholds: SLAThresholds = DEFAULT_SLA_THRESHOLDS,
) -> dict[str, float]:
    """Render the live threshold tuple as a flat dict.

    Used by ``/health/sla`` probes, debug logs, and the alert-rule YAML
    generator. Single SSoT for the threshold names so YAML keys and
    Python attribute names stay aligned.
    """
    return {
        "p95_warn_seconds": thresholds.p95_warn_seconds,
        "p95_critical_seconds": thresholds.p95_critical_seconds,
        "error_rate_warn": thresholds.error_rate_warn,
        "error_rate_critical": thresholds.error_rate_critical,
        "cache_hit_ratio_warn": thresholds.cache_hit_ratio_warn,
        "circuit_open_duration_s": thresholds.circuit_open_duration_s,
        "breach_window_s": thresholds.breach_window_s,
    }


def thresholds_from_config(config: dict[str, float] | None) -> SLAThresholds:
    """Build an ``SLAThresholds`` from a ``system_config`` dict, with fallback.

    Missing keys fall through to the module-level defaults (declared in
    ``shared.constants``). The expected dict shape is the same one
    returned by ``sla_threshold_snapshot`` — symmetric encode/decode.
    """
    if not config:
        return DEFAULT_SLA_THRESHOLDS
    return SLAThresholds(
        p95_warn_seconds=float(
            config.get("p95_warn_seconds", DEFAULT_SLA_P95_WARN_SECONDS),
        ),
        p95_critical_seconds=float(
            config.get("p95_critical_seconds", DEFAULT_SLA_P95_CRITICAL_SECONDS),
        ),
        error_rate_warn=float(
            config.get("error_rate_warn", DEFAULT_SLA_ERROR_RATE_WARN),
        ),
        error_rate_critical=float(
            config.get("error_rate_critical", DEFAULT_SLA_ERROR_RATE_CRITICAL),
        ),
        cache_hit_ratio_warn=float(
            config.get("cache_hit_ratio_warn", DEFAULT_SLA_CACHE_HIT_RATIO_WARN),
        ),
        circuit_open_duration_s=float(
            config.get("circuit_open_duration_s", DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S),
        ),
        breach_window_s=float(
            config.get("breach_window_s", DEFAULT_SLA_BREACH_WINDOW_S),
        ),
    )


__all__ = [
    "DEFAULT_SLA_THRESHOLDS",
    "SLAStatus",
    "SLAThresholds",
    "classify_cache_hit_ratio",
    "classify_circuit_open_duration",
    "classify_error_rate",
    "classify_latency",
    "sla_threshold_snapshot",
    "thresholds_from_config",
]
