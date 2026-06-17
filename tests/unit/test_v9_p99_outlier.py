"""p99 latency outlier guard tests.

Verifies the helper that translates a chat-request duration into the
``ragbot_chat_p99_outlier_total`` Prometheus counter + ``chat_latency_outlier``
structured warning. Counter cardinality stays bounded: only three latency
buckets and the resolved intent label.
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.observability import p99_outlier
from ragbot.infrastructure.observability.metrics import chat_p99_outlier_total
from ragbot.shared.constants import DEFAULT_P99_OUTLIER_THRESHOLD_S


def _counter_value(intent: str, bucket: str) -> float:
    """Read the counter sample for a single label set.

    Walks the prometheus_client samples — labelled counters surface as
    ``<name>_total`` family rows tagged with the label dict.
    """
    for metric in chat_p99_outlier_total.collect():
        for sample in metric.samples:
            if not sample.name.endswith("_total"):
                continue
            if (
                sample.labels.get("intent") == intent
                and sample.labels.get("latency_bucket") == bucket
            ):
                return sample.value
    return 0.0


@pytest.mark.parametrize(
    ("duration_s", "expected_bucket"),
    [
        (DEFAULT_P99_OUTLIER_THRESHOLD_S + 0.5, "20-30"),
        (29.99, "20-30"),
        (30.0, "30-60"),
        (45.7, "30-60"),
        (60.0, "60+"),
        (180.0, "60+"),
    ],
)
def test_latency_bucket_classification(duration_s: float, expected_bucket: str) -> None:
    assert p99_outlier.latency_bucket(duration_s) == expected_bucket


def test_under_threshold_does_not_increment() -> None:
    intent = "factoid"
    bucket = "20-30"
    before = _counter_value(intent, bucket)
    classified = p99_outlier.record_chat_latency(
        duration_s=DEFAULT_P99_OUTLIER_THRESHOLD_S - 0.001,
        intent=intent,
    )
    assert classified is False
    assert _counter_value(intent, bucket) == pytest.approx(before)


def test_outlier_increments_counter_and_returns_true() -> None:
    intent = "comparison"
    bucket = "20-30"
    before = _counter_value(intent, bucket)
    classified = p99_outlier.record_chat_latency(
        duration_s=DEFAULT_P99_OUTLIER_THRESHOLD_S + 1.0,
        intent=intent,
    )
    assert classified is True
    after = _counter_value(intent, bucket)
    assert after == pytest.approx(before + 1.0)


def test_long_outlier_falls_into_60_plus_bucket() -> None:
    intent = "multi_hop"
    bucket = "60+"
    before = _counter_value(intent, bucket)
    classified = p99_outlier.record_chat_latency(
        duration_s=75.0,
        intent=intent,
    )
    assert classified is True
    assert _counter_value(intent, bucket) == pytest.approx(before + 1.0)


def test_missing_intent_falls_back_to_unknown_label() -> None:
    bucket = "20-30"
    before = _counter_value("unknown", bucket)
    classified = p99_outlier.record_chat_latency(
        duration_s=DEFAULT_P99_OUTLIER_THRESHOLD_S + 0.1,
        intent=None,
    )
    assert classified is True
    assert _counter_value("unknown", bucket) == pytest.approx(before + 1.0)


def test_blank_intent_normalises_to_unknown() -> None:
    bucket = "20-30"
    before = _counter_value("unknown", bucket)
    classified = p99_outlier.record_chat_latency(
        duration_s=DEFAULT_P99_OUTLIER_THRESHOLD_S + 0.1,
        intent="   ",
    )
    assert classified is True
    assert _counter_value("unknown", bucket) == pytest.approx(before + 1.0)


def test_helper_never_raises_on_invalid_duration() -> None:
    # Negative durations cannot be outliers — helper returns False, no crash.
    assert (
        p99_outlier.record_chat_latency(duration_s=-1.0, intent="factoid") is False
    )
