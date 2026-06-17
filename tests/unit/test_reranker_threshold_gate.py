"""Reranker post-rerank refuse threshold gate.

When a real cross-encoder reranker has actually run, the gate inspects
the top-1 surviving score against a per-bot resolved floor
(``reranker_min_score_active``). Below-floor → drop every chunk so the
existing refuse short-circuit at the generate node emits the bot's
``oos_answer_template`` (no application-injected text, no LLM override:
Quality Gate #10).

The gate is mode-aware: bypass paths (NullReranker, disabled, RRF
fallback) leave chunks untouched because their score scale is
incomparable with the cross-encoder 0..1 floor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ragbot.orchestration.query_graph import _rerank_threshold_gate
from ragbot.shared import bot_limits
from ragbot.shared.constants import DEFAULT_RERANKER_MIN_SCORE_ACTIVE


# ---------------------------------------------------------------------------
# Constant + schema pinning
# ---------------------------------------------------------------------------

def test_default_threshold_value_is_calibrated_floor() -> None:
    """Constant must match the calibrated cross-encoder floor (0.30)."""
    assert DEFAULT_RERANKER_MIN_SCORE_ACTIVE == 0.30


def test_plan_limit_schema_default_tracks_constant() -> None:
    """Per-bot override entry must default to the same constant — no drift."""
    schema = bot_limits.PLAN_LIMIT_SCHEMA["reranker_min_score_active"]
    assert schema["default"] == DEFAULT_RERANKER_MIN_SCORE_ACTIVE
    assert schema["default"] == 0.30
    assert schema["type"] == "float"
    assert schema["min"] == 0.0
    assert schema["max"] == 1.0


# ---------------------------------------------------------------------------
# Gate behaviour — boundary + refuse + pass
# ---------------------------------------------------------------------------

def _chunks(scores: list[float]) -> list[dict]:
    return [{"score": s, "content": f"c{i}", "chunk_id": f"id{i}"}
            for i, s in enumerate(scores)]


def test_top_score_below_threshold_refuses() -> None:
    """top_score=0.29 with threshold=0.30 → gate drops all chunks (refuse)."""
    out, meta = _rerank_threshold_gate(
        _chunks([0.29, 0.20, 0.10]), threshold=0.30, mode="rerank",
    )
    assert out == []
    assert meta["refused"] is True
    assert meta["applicable"] is True
    assert meta["top_score"] == 0.29
    assert meta["threshold"] == 0.30


def test_top_score_above_threshold_passes() -> None:
    """top_score=0.31 with threshold=0.30 → chunks pass through unchanged."""
    chunks = _chunks([0.31, 0.10])
    out, meta = _rerank_threshold_gate(chunks, threshold=0.30, mode="rerank")
    assert out == chunks
    assert meta["refused"] is False
    assert meta["applicable"] is True
    assert meta["top_score"] == 0.31


def test_top_score_equal_to_threshold_passes_boundary() -> None:
    """top_score == threshold is admitted (>= comparison, not >)."""
    chunks = _chunks([0.30, 0.05])
    out, meta = _rerank_threshold_gate(chunks, threshold=0.30, mode="rerank")
    assert out == chunks
    assert meta["refused"] is False
    assert meta["top_score"] == 0.30


# ---------------------------------------------------------------------------
# Per-bot override scenarios (override of resolved threshold value)
# ---------------------------------------------------------------------------

def test_per_bot_override_higher_threshold_refuses_marginal() -> None:
    """Per-bot override raises floor to 0.5 → top=0.40 now fails."""
    out, meta = _rerank_threshold_gate(
        _chunks([0.40, 0.20]), threshold=0.50, mode="rerank",
    )
    assert out == []
    assert meta["refused"] is True
    assert meta["threshold"] == 0.50
    assert meta["top_score"] == 0.40


def test_per_bot_override_higher_threshold_passes_strong_hit() -> None:
    """Per-bot override 0.5; top=0.60 passes."""
    chunks = _chunks([0.60, 0.30])
    out, meta = _rerank_threshold_gate(chunks, threshold=0.50, mode="rerank")
    assert out == chunks
    assert meta["refused"] is False
    assert meta["threshold"] == 0.50
    assert meta["top_score"] == 0.60


# ---------------------------------------------------------------------------
# Mode-aware bypass — never gate when no real reranker ran
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bypass_mode",
    [
        "null_reranker",
        "disabled",
        "no_reranker",
        "empty_input",
        "intent_skip",
        "intent_skip_set",
        "rerank_fallback",
    ],
)
def test_bypass_modes_do_not_gate(bypass_mode: str) -> None:
    """Bypass modes leave chunks untouched — RRF / placeholder scores
    are not comparable with the cross-encoder 0..1 floor."""
    chunks = _chunks([0.01, 0.005])  # RRF-shaped, would otherwise fail
    out, meta = _rerank_threshold_gate(chunks, threshold=0.30, mode=bypass_mode)
    assert out == chunks
    assert meta["refused"] is False
    assert meta["applicable"] is False


def test_empty_input_returns_empty_no_refuse_decision() -> None:
    """Empty input is a no-op — there is no top-1 to evaluate."""
    out, meta = _rerank_threshold_gate([], threshold=0.30, mode="rerank")
    assert out == []
    assert meta["applicable"] is False
    assert meta["refused"] is False


# ---------------------------------------------------------------------------
# Refuse text origin — gate MUST NOT inject text (Quality Gate #10)
# ---------------------------------------------------------------------------

def test_gate_returns_only_chunks_and_meta_never_text() -> None:
    """Gate signature must NEVER produce refuse text — that's the bot's
    ``oos_answer_template`` job downstream. This pins the contract."""
    out, meta = _rerank_threshold_gate(
        _chunks([0.05]), threshold=0.30, mode="rerank",
    )
    # Tuple of (list[dict], dict[str, Any]); no string payload.
    assert isinstance(out, list)
    assert isinstance(meta, dict)
    # No refuse-text key smuggled inside meta.
    assert "answer" not in meta
    assert "refuse_text" not in meta
    assert "oos_answer_template" not in meta


def test_refuse_text_flows_from_bot_oos_template_constant() -> None:
    """When the gate refuses, downstream refuse short-circuit consumes
    ``bots.oos_answer_template`` (via ``_oos_text`` helper). Static-text
    assertion that the rerank node never imports an i18n refuse string."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ragbot"
        / "orchestration"
        / "query_graph.py"
    )
    body = src.read_text(encoding="utf-8") + "".join(p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py")))
    # The refuse short-circuit reads the bot template.
    assert "_oos_text(state)" in body
    assert 'oos_answer_template' in body
    # And the gate wiring delegates to that path — it MUST NOT rewrite
    # the answer in place.
    gate_block_start = body.find("Post-filter refuse gate")
    assert gate_block_start > 0, "gate comment marker missing"
    # 3000-char window accommodates the Wave J2 comment block that explains
    # the cliff-strategy gate-skip + the conditional ``if _run_gate:`` wrapper.
    gate_block = body[gate_block_start:gate_block_start + 3000]
    # Gate emits structlog event + drops chunks; never overwrites answer.
    assert "rerank_threshold_gate" in gate_block
    assert "_rerank_threshold_gate" in gate_block
    assert "state[\"answer\"]" not in gate_block  # no answer write here


# ---------------------------------------------------------------------------
# Structlog event field contract
# ---------------------------------------------------------------------------

def test_structlog_event_carries_required_fields() -> None:
    """The rerank_threshold_gate emit site must include observability
    fields (top_score, threshold, refused, bot_id) — pinned via static
    text so an over-zealous refactor cannot strip them silently."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ragbot"
        / "orchestration"
        / "query_graph.py"
    )
    body = src.read_text(encoding="utf-8") + "".join(p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py")))
    emit_idx = body.find('"rerank_threshold_gate"')
    assert emit_idx > 0, "structlog event missing"
    # Look in a window after the event name for the kwargs.
    window = body[emit_idx:emit_idx + 600]
    assert "top_score=" in window
    assert "threshold=" in window
    assert "refused=" in window
    assert "bot_id=" in window
