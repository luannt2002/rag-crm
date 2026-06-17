"""Shared helpers for load-test harness scripts (test-side classification only).

Canonical fragment list = ``DEFAULT_LOADTEST_REFUSE_PATTERNS`` in shared.constants.
Per CLAUDE.md, this regex never feeds the production pipeline.
"""

from __future__ import annotations

import os
import re
import sys
from re import Pattern
from typing import Final

# Inject src/ on sys.path when imported by harnesses that don't.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:  # pragma: no cover
    sys.path.insert(0, _SRC)

from ragbot.shared.constants import DEFAULT_LOADTEST_REFUSE_PATTERNS  # noqa: E402

# Batch tuning lives here (test-tooling), not shared/constants.py — these
# knobs only affect the harness, never the production pipeline.

# 0 = no batch mode (single-shot). N>0 splits into batches of N with checkpoint.
DEFAULT_LOADTEST_BATCH_SIZE: Final[int] = 0

# Top-K worst REFUSE_NO_DOCS questions surfaced per-batch markdown.
DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE: Final[int] = 3

# Question preview cap inside the batch markdown table.
DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS: Final[int] = 80


def make_refuse_pattern(*, extra: tuple[str, ...] = ()) -> Pattern[str]:
    """Build a compiled refuse-cue regex; `extra` appends harness-specific cues."""
    fragments = (*DEFAULT_LOADTEST_REFUSE_PATTERNS, *extra)
    return re.compile("(" + "|".join(fragments) + ")", re.IGNORECASE)


REFUSE_PATTERN: Pattern[str] = make_refuse_pattern()


def is_refuse(text: str | None, *, pattern: Pattern[str] | None = None) -> bool:
    """Return True if text trips the heuristic refuse pattern. None/empty → False."""
    if not text:
        return False
    pat = pattern or REFUSE_PATTERN
    return bool(pat.search(text))


__all__ = [
    "DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS",
    "DEFAULT_LOADTEST_BATCH_SIZE",
    "DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE",
    "DEFAULT_LOADTEST_REFUSE_PATTERNS",
    "make_refuse_pattern",
    "REFUSE_PATTERN",
    "is_refuse",
]
