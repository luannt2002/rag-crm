"""Unit tests — SLA monitoring layer (Phase D Stream D3).

Covers:
- threshold classification precedence (CRITICAL beats WARN beats OK)
- negative / out-of-range sample guard
- circuit-open duration band
- cache hit ratio inversion (low ratio = WARN)
- snapshot round-trip vs ``thresholds_from_config``
- alert YAML smoke parse + key surface
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ragbot.infrastructure.observability.sla_metrics import (
    DEFAULT_SLA_THRESHOLDS,
    SLAStatus,
    SLAThresholds,
    classify_cache_hit_ratio,
    classify_circuit_open_duration,
    classify_error_rate,
    classify_latency,
    sla_threshold_snapshot,
    thresholds_from_config,
)
from ragbot.shared.constants import (
    DEFAULT_SLA_BREACH_WINDOW_S,
    DEFAULT_SLA_CACHE_HIT_RATIO_WARN,
    DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S,
    DEFAULT_SLA_ERROR_RATE_CRITICAL,
    DEFAULT_SLA_ERROR_RATE_WARN,
    DEFAULT_SLA_P95_CRITICAL_SECONDS,
    DEFAULT_SLA_P95_WARN_SECONDS,
)


# ---------------------------------------------------------------------------
# Latency classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p95_seconds", "expected"),
    [
        (0.0, SLAStatus.OK),
        (1.0, SLAStatus.OK),
        (DEFAULT_SLA_P95_WARN_SECONDS - 0.001, SLAStatus.OK),
        (DEFAULT_SLA_P95_WARN_SECONDS, SLAStatus.WARN),
        (DEFAULT_SLA_P95_WARN_SECONDS + 1.0, SLAStatus.WARN),
        (DEFAULT_SLA_P95_CRITICAL_SECONDS - 0.001, SLAStatus.WARN),
        (DEFAULT_SLA_P95_CRITICAL_SECONDS, SLAStatus.CRITICAL),
        (DEFAULT_SLA_P95_CRITICAL_SECONDS + 60.0, SLAStatus.CRITICAL),
        # Negative durations are a corrupt sample — must NOT page.
        (-1.0, SLAStatus.OK),
    ],
)
def test_classify_latency_bands(p95_seconds: float, expected: SLAStatus) -> None:
    """p95 latency lands in the correct band with CRITICAL-first precedence."""
    assert classify_latency(p95_seconds) is expected


# ---------------------------------------------------------------------------
# Error-rate classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error_rate", "expected"),
    [
        (0.0, SLAStatus.OK),
        (DEFAULT_SLA_ERROR_RATE_WARN - 0.0001, SLAStatus.OK),
        (DEFAULT_SLA_ERROR_RATE_WARN, SLAStatus.WARN),
        (DEFAULT_SLA_ERROR_RATE_CRITICAL - 0.0001, SLAStatus.WARN),
        (DEFAULT_SLA_ERROR_RATE_CRITICAL, SLAStatus.CRITICAL),
        # Out-of-range high value must still trip CRITICAL, not be swallowed.
        (1.2, SLAStatus.CRITICAL),
        # Negative → corrupt counter snapshot — treat as OK.
        (-0.5, SLAStatus.OK),
    ],
)
def test_classify_error_rate_bands(error_rate: float, expected: SLAStatus) -> None:
    """Error-rate classifier matches the warn / critical defaults exactly."""
    assert classify_error_rate(error_rate) is expected


# ---------------------------------------------------------------------------
# Cache hit ratio classifier (LOW ratio = warn — inverted direction)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hit_ratio", "expected"),
    [
        (0.0, SLAStatus.WARN),
        (DEFAULT_SLA_CACHE_HIT_RATIO_WARN - 0.001, SLAStatus.WARN),
        (DEFAULT_SLA_CACHE_HIT_RATIO_WARN, SLAStatus.OK),
        (0.5, SLAStatus.OK),
        (1.0, SLAStatus.OK),
        # Out-of-range guards: -0.1 and 1.5 are corrupt → no alarm.
        (-0.1, SLAStatus.OK),
        (1.5, SLAStatus.OK),
    ],
)
def test_classify_cache_hit_ratio_bands(
    hit_ratio: float, expected: SLAStatus,
) -> None:
    """Cache hit ratio: low → WARN, healthy → OK; corrupt samples ignored."""
    assert classify_cache_hit_ratio(hit_ratio) is expected


# ---------------------------------------------------------------------------
# Circuit-open duration classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration_s", "expected"),
    [
        (0.0, SLAStatus.OK),
        (DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S - 1.0, SLAStatus.OK),
        (DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S, SLAStatus.CRITICAL),
        (DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S * 3, SLAStatus.CRITICAL),
        (-5.0, SLAStatus.OK),
    ],
)
def test_classify_circuit_open_duration_bands(
    duration_s: float, expected: SLAStatus,
) -> None:
    """Circuit OPEN > threshold → CRITICAL; under-threshold or corrupt → OK."""
    assert classify_circuit_open_duration(duration_s) is expected


# ---------------------------------------------------------------------------
# Threshold snapshot + config round-trip
# ---------------------------------------------------------------------------


def test_sla_threshold_snapshot_contains_all_defaults() -> None:
    """Snapshot exposes every threshold key with the constant default value."""
    snap = sla_threshold_snapshot()
    assert snap["p95_warn_seconds"] == DEFAULT_SLA_P95_WARN_SECONDS
    assert snap["p95_critical_seconds"] == DEFAULT_SLA_P95_CRITICAL_SECONDS
    assert snap["error_rate_warn"] == DEFAULT_SLA_ERROR_RATE_WARN
    assert snap["error_rate_critical"] == DEFAULT_SLA_ERROR_RATE_CRITICAL
    assert snap["cache_hit_ratio_warn"] == DEFAULT_SLA_CACHE_HIT_RATIO_WARN
    assert (
        snap["circuit_open_duration_s"] == DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S
    )
    assert snap["breach_window_s"] == DEFAULT_SLA_BREACH_WINDOW_S


def test_thresholds_from_config_overrides_defaults() -> None:
    """system_config overrides apply; missing keys fall through to defaults."""
    override = {
        "p95_warn_seconds": 7.5,
        "p95_critical_seconds": 12.0,
        "error_rate_warn": 0.03,
        # error_rate_critical, cache_hit_ratio_warn, circuit_open_duration_s,
        # breach_window_s intentionally absent → should keep defaults.
    }
    th = thresholds_from_config(override)
    assert th.p95_warn_seconds == 7.5
    assert th.p95_critical_seconds == 12.0
    assert th.error_rate_warn == 0.03
    # Fall-through path.
    assert th.error_rate_critical == DEFAULT_SLA_ERROR_RATE_CRITICAL
    assert th.cache_hit_ratio_warn == DEFAULT_SLA_CACHE_HIT_RATIO_WARN
    assert th.circuit_open_duration_s == DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S
    assert th.breach_window_s == DEFAULT_SLA_BREACH_WINDOW_S


def test_thresholds_from_config_none_returns_default_singleton() -> None:
    """``None`` input returns the module-level frozen default."""
    assert thresholds_from_config(None) is DEFAULT_SLA_THRESHOLDS
    assert thresholds_from_config({}) is DEFAULT_SLA_THRESHOLDS


def test_custom_thresholds_used_for_classification() -> None:
    """Custom thresholds plumb through classifiers without monkey-patching."""
    custom = SLAThresholds(p95_warn_seconds=2.0, p95_critical_seconds=4.0)
    # 3.0s with the custom thresholds = WARN; with defaults (10s/15s) = OK.
    assert classify_latency(3.0, thresholds=custom) is SLAStatus.WARN
    assert classify_latency(3.0) is SLAStatus.OK
    assert classify_latency(5.0, thresholds=custom) is SLAStatus.CRITICAL


# ---------------------------------------------------------------------------
# Alert YAML smoke test — load it, assert expected rule names + groups.
# ---------------------------------------------------------------------------


def _alert_yaml_path() -> Path:
    here = Path(__file__).resolve()
    # tests/unit/test_sla_metrics.py -> repo_root/tests/unit
    repo_root = here.parents[2]
    return repo_root / "scripts" / "sla_alerting_rules.yaml"


def test_alert_rules_yaml_parses_with_required_rules() -> None:
    """Alert YAML is well-formed and ships the rules promised by the spec."""
    path = _alert_yaml_path()
    assert path.is_file(), f"missing alert rule file: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "groups" in data and isinstance(data["groups"], list)
    rule_names: set[str] = set()
    for group in data["groups"]:
        assert "name" in group
        assert "rules" in group
        for rule in group["rules"]:
            assert "alert" in rule
            assert "expr" in rule
            assert "for" in rule
            assert "labels" in rule
            assert rule["labels"].get("severity") in {"warning", "critical"}
            rule_names.add(rule["alert"])
    # Spec requires: p95 warn, p95 critical, cache hit ratio low,
    # circuit-breaker stuck open, grounding-fail (HALLU sacred).
    required = {
        "RagbotP95LatencyWarn",
        "RagbotP95LatencyCritical",
        "RagbotCacheHitRatioLow",
        "RagbotCircuitBreakerStuckOpen",
        "RagbotGroundingFailSpike",
    }
    missing = required - rule_names
    assert not missing, f"alert YAML missing required rules: {missing}"
