"""Floor thresholds for the vertical-agnostic golden test harness.

These are default floors applied when a fixture YAML does not override them.
A fixture can lower or raise its own ``floor:`` block as appropriate for
the vertical (some industries tolerate higher latency, some require stricter
faithfulness).

Test-only constants live here (NOT in ``src/ragbot/shared/constants.py``)
because they are only consumed by the eval harness. Production code never
imports this module.
"""

from __future__ import annotations

from typing import Final

# Minimum fraction of questions that must PASS the per-question rubric.
DEFAULT_GOLDEN_PASS_RATE_FLOOR: Final[float] = 0.60

# Minimum mean faithfulness (grounding score) across the question set.
DEFAULT_GOLDEN_FAITH_FLOOR: Final[float] = 0.85

# Minimum mean top retrieval score across the question set.
DEFAULT_GOLDEN_TOP_SCORE_FLOOR: Final[float] = 0.45

# Maximum acceptable p95 end-to-end latency (milliseconds).
DEFAULT_GOLDEN_P95_FLOOR_MS: Final[int] = 8000

# Maximum acceptable hallucination count (rubric violations) — 0 by default.
DEFAULT_GOLDEN_HALLU_FLOOR: Final[int] = 0

# Default per-request timeout when the harness calls the chat endpoint.
DEFAULT_GOLDEN_REQUEST_TIMEOUT_S: Final[float] = 60.0

# Default chat endpoint path; can be overridden via ``GoldenTestRunner``
# constructor argument.
DEFAULT_GOLDEN_CHAT_PATH: Final[str] = "/api/ragbot/test/chat"

# Percentile rank for the latency floor (95 = p95).
DEFAULT_GOLDEN_LATENCY_PERCENTILE: Final[float] = 95.0

# Milliseconds per second — used when converting timeout (in seconds) to
# the latency_ms placeholder reported on HTTP failures.
DEFAULT_GOLDEN_MS_PER_SECOND: Final[int] = 1000
