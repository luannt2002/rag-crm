"""Smoke tests for Task 5 — Prometheus metrics emit.

Verify that:
- Metric collectors exist with expected labels.
- `.inc()` / `.observe()` on each increments the Prometheus value accordingly.
- `MetricsRegistry` facade exposes the same collectors.
- `/metrics` endpoint renders exposition format containing new metric names.
"""

from __future__ import annotations

from prometheus_client import generate_latest

from ragbot.infrastructure.observability.metrics import (
    MetricsRegistry,
    REGISTRY,
    cost_usd_total,
    grounding_fail_total,
    guardrail_triggered_total,
    model_invocation_total,
    request_duration_seconds,
    request_total,
    step_duration_seconds,
    tokens_used_total,
)


def _get_sample(metric_name: str, labels: dict[str, str] | None = None) -> float:
    """Pull a single sample value from the shared `REGISTRY`."""
    for fam in REGISTRY.collect():
        for sample in fam.samples:
            if sample.name != metric_name:
                continue
            if labels is None or all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return float(sample.value)
    return 0.0


def test_metrics_registry_exposes_all_collectors() -> None:
    assert MetricsRegistry.request_total is request_total
    assert MetricsRegistry.request_duration_seconds is request_duration_seconds
    assert MetricsRegistry.step_duration_seconds is step_duration_seconds
    assert MetricsRegistry.tokens_used_total is tokens_used_total
    assert MetricsRegistry.cost_usd_total is cost_usd_total
    assert MetricsRegistry.guardrail_triggered_total is guardrail_triggered_total
    assert MetricsRegistry.grounding_fail_total is grounding_fail_total
    assert MetricsRegistry.model_invocation_total is model_invocation_total


def test_request_total_increments() -> None:
    labels = {"status": "success", "channel_type": "test-smoke"}
    before = _get_sample("request_total", labels)
    request_total.labels(**labels).inc()
    request_total.labels(**labels).inc(2)
    after = _get_sample("request_total", labels)
    assert after - before == 3.0


def test_request_duration_observes() -> None:
    before = _get_sample("request_duration_seconds_count")
    request_duration_seconds.observe(0.123)
    after = _get_sample("request_duration_seconds_count")
    assert after - before == 1.0


def test_step_duration_observes() -> None:
    labels = {"step_name": "smoke_step"}
    before = _get_sample("step_duration_seconds_count", labels)
    step_duration_seconds.labels(**labels).observe(0.01)
    after = _get_sample("step_duration_seconds_count", labels)
    assert after - before == 1.0


def test_tokens_and_cost_increment() -> None:
    tlabels = {"purpose": "generation", "model_id": "m-test", "kind": "prompt"}
    clabels = {"purpose": "generation", "model_id": "m-test"}
    t_before = _get_sample("tokens_used_total", tlabels)
    c_before = _get_sample("cost_usd_total", clabels)
    tokens_used_total.labels(**tlabels).inc(42)
    cost_usd_total.labels(**clabels).inc(0.25)
    assert _get_sample("tokens_used_total", tlabels) - t_before == 42.0
    assert abs(_get_sample("cost_usd_total", clabels) - c_before - 0.25) < 1e-9


def test_guardrail_and_grounding() -> None:
    glabels = {"rule_id": "prompt_injection", "severity": "block", "action": "block"}
    g_before = _get_sample("guardrail_triggered_total", glabels)
    guardrail_triggered_total.labels(**glabels).inc()
    assert _get_sample("guardrail_triggered_total", glabels) - g_before == 1.0

    gf_before = _get_sample("grounding_fail_total")
    grounding_fail_total.inc()
    assert _get_sample("grounding_fail_total") - gf_before == 1.0


def test_model_invocation_total() -> None:
    labels = {"purpose": "generation", "provider": "test", "status": "success"}
    before = _get_sample("model_invocation_total", labels)
    model_invocation_total.labels(**labels).inc()
    assert _get_sample("model_invocation_total", labels) - before == 1.0


def test_prometheus_exposition_contains_new_metrics() -> None:
    body = generate_latest(REGISTRY).decode("utf-8")
    for name in (
        "request_total",
        "request_duration_seconds",
        "step_duration_seconds",
        "tokens_used_total",
        "cost_usd_total",
        "guardrail_triggered_total",
        "grounding_fail_total",
        "model_invocation_total",
    ):
        assert name in body, f"missing metric {name} in /metrics exposition"
