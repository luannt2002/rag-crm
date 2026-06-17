"""Reranker defaults must reflect the calibrated cross-encoder distribution.

Two changes pinned here:

* ``DEFAULT_RERANKER_MIN_SCORE_ACTIVE`` floor reflects empirical ZE rerank
  distribution: scores in the 0.30+ band correlate with relevant
  retrievals while 0.05–0.15 is too permissive and lets noise through,
  contributing to HALLU risk when the generate node receives weakly
  grounded chunks. The post-rerank refuse gate uses this value unless a
  bot's plan_limits override raises it.

* ``DEFAULT_RERANK_FILTER_STRATEGY`` flips from ``"threshold"`` to
  ``"cliff"`` so the system-wide default cannot return zero chunks.
  Cliff-detect's ``force_min_keep=True`` always preserves at least one
  chunk when input was non-empty — the refuse short-circuit at the
  generate node still fires only when the threshold gate (or grade
  judgement) cuts the chunk list to empty.
"""

from __future__ import annotations

from ragbot.shared import bot_limits
from ragbot.shared.constants import (
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
)


def test_default_reranker_min_score_active_is_calibrated_floor() -> None:
    assert DEFAULT_RERANKER_MIN_SCORE_ACTIVE == 0.30, (
        "Empirical ZE rerank distribution: 0.3+ correlates with relevant "
        "retrievals; 0.05-0.15 was too permissive and let noise reach the "
        "generate node, contributing to HALLU risk. The PLAN_LIMIT_SCHEMA "
        "default tracks this constant so per-bot tuning and the system "
        "default agree."
    )


def test_default_rerank_filter_strategy_is_cliff() -> None:
    assert DEFAULT_RERANK_FILTER_STRATEGY == "cliff", (
        "threshold strategy can return [] when every reranker score sits "
        "below the floor. cliff-detect's force_min_keep=True guarantees "
        "at least one chunk reaches grade when input was non-empty, "
        "preventing the silent refuse short-circuit."
    )


def test_plan_limit_schema_default_for_min_score_matches_constant() -> None:
    """Constant and validation schema must not drift."""
    schema = bot_limits.PLAN_LIMIT_SCHEMA["reranker_min_score_active"]
    assert schema["default"] == DEFAULT_RERANKER_MIN_SCORE_ACTIVE


def test_plan_limit_schema_default_for_strategy_matches_constant() -> None:
    schema = bot_limits.PLAN_LIMIT_SCHEMA["rerank_filter_strategy"]
    assert schema["default"] == DEFAULT_RERANK_FILTER_STRATEGY
    assert "cliff" in schema["options"]
    assert "threshold" in schema["options"]
