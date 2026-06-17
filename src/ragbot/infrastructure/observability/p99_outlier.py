"""p99 latency outlier guard.

Records a Prometheus counter + structured warning when an end-to-end chat
request crosses the ``DEFAULT_P99_OUTLIER_THRESHOLD_S`` threshold. Pure
helper — no application state, no imports from ``orchestration`` or any
business module — so it can be called from any sink (chat_worker, REST
handler, future stream surface).

Bucket labels are low-cardinality on purpose: Prometheus cardinality is the
operator's bill, not ours. Three buckets are enough to distinguish a
single 25 s slow request from a sustained 60 s+ regression in Grafana.
"""

from __future__ import annotations

from typing import Final

import structlog

from ragbot.infrastructure.observability.metrics import chat_p99_outlier_total
from ragbot.shared.constants import DEFAULT_P99_OUTLIER_THRESHOLD_S

logger = structlog.get_logger(__name__)

_BUCKET_20_30: Final[str] = "20-30"
_BUCKET_30_60: Final[str] = "30-60"
_BUCKET_60_PLUS: Final[str] = "60+"
_INTENT_UNKNOWN: Final[str] = "unknown"

# Boundaries align with the bucket labels above. Kept as module-level
# constants so the helper has zero magic literals.
_BUCKET_LOW_BOUND_S: Final[float] = 30.0
_BUCKET_MID_BOUND_S: Final[float] = 60.0


def latency_bucket(duration_s: float) -> str:
    """Classify a duration (seconds) into a coarse outlier bucket label.

    Caller is responsible for first checking the threshold; this only maps a
    duration that is already known to be ``>= DEFAULT_P99_OUTLIER_THRESHOLD_S``
    to one of three labels: ``"20-30"``, ``"30-60"``, ``"60+"``.
    """
    if duration_s < _BUCKET_LOW_BOUND_S:
        return _BUCKET_20_30
    if duration_s < _BUCKET_MID_BOUND_S:
        return _BUCKET_30_60
    return _BUCKET_60_PLUS


def record_chat_latency(
    *,
    duration_s: float,
    intent: str | None,
    threshold_s: float = DEFAULT_P99_OUTLIER_THRESHOLD_S,
) -> bool:
    """Record a chat-request duration; bump outlier counter on threshold breach.

    Returns ``True`` when the request was classified as an outlier (counter
    incremented + warning logged), ``False`` otherwise. Best-effort — never
    raises so the caller can drop this on the post-response hot-path
    without try/except scaffolding.
    """
    if duration_s < threshold_s:
        return False
    try:
        bucket = latency_bucket(duration_s)
        intent_label = (intent or _INTENT_UNKNOWN).strip() or _INTENT_UNKNOWN
        chat_p99_outlier_total.labels(
            intent=intent_label,
            latency_bucket=bucket,
        ).inc()
    except (ValueError, TypeError, AttributeError):
        # Counter math failures must not poison the response path.
        # ValueError = negative duration; TypeError = None arithmetic;
        # AttributeError = Counter mocked out in tests. Silent swallow —
        # the observability layer cannot observe its own emit failures.
        return False
    # Logger.warning is best-effort: structlog can raise
    # ``ValueError: I/O operation on closed file`` when pytest
    # capsys teardown closes a captured stream after the cached handle
    # was bound. That must not surface to chat_worker callers.
    try:
        logger.warning(
            "chat_latency_outlier",
            duration_s=round(duration_s, 3),
            intent=intent_label,
            latency_bucket=bucket,
            threshold_s=threshold_s,
            latency_outlier=True,
        )
    except (ValueError, OSError, AttributeError):
        # Swallow log-sink failures (closed stdout/stderr in tests,
        # broken pipe on container shutdown, structlog reconfigured to
        # a None sink). The counter increment above already fired; the
        # operator alert path is intact.
        pass
    return True


__all__ = ["latency_bucket", "record_chat_latency"]
