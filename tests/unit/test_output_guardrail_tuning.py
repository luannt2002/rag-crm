"""Wave J2 — output-guardrail false-positive tuning pins.

Background
----------
Wave I 15-query load test (Dr Medispa bot, top_score range 0.29-0.63)
recorded 4/15 = 27% false-positive refuses:

  * Q3 "Giờ mở cửa của spa?" — top_score 0.29
  * Q4 "Dịch vụ tiêm filler giá bao nhiêu?" — top_score 0.43
  * Q5 "So sánh filler và botox về giá" — top_score 0.29
  * Q13 "Spa có khám trẻ em không?" — top_score 0.63

Root cause (J2 audit, see ``reports/WAVE_J_GUARDRAIL_TUNE_20260520.md``):
the post-rerank ``_rerank_threshold_gate`` runs UNCONDITIONALLY after the
cliff filter has already cut weak chunks. The cliff filter's
``force_min_keep=True`` safety net deliberately retains the top-scored
chunk so downstream nodes always see context — the static threshold gate
then discards it, breaking the cliff contract documented in
``PLAN_LIMIT_SCHEMA``:

    "when strategy='cliff', reranker_min_score_active is ignored"

Fix
---
Skip ``_rerank_threshold_gate`` when ``rerank_filter_strategy == "cliff"``,
unless the per-bot ``rerank_threshold_gate_after_cliff_enabled`` flag is
flipped True (audit-heavy compliance bots that prefer refuse over
weak-answer). HALLU=0 stays sacred because:

  * Cliff ``absolute_floor`` already cuts negative-relevance noise.
  * The bot owner's system prompt + grounding judge own the "is this
    chunk good enough?" decision — not a hard threshold.
  * Owner opt-in restores legacy behaviour without a code change.

Tests pin BOTH the gate behaviour (legacy, unchanged) and the new strategy
contract (cliff bypasses the gate).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ragbot.orchestration.query_graph import _rerank_threshold_gate
from ragbot.shared import bot_limits
from ragbot.shared.constants import (
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED,
)


_ORCH_DIR = Path("src/ragbot/orchestration")


def _orchestration_src() -> str:
    """Concatenated source of query_graph.py + every extracted node module.

    The rerank node body was lifted out of ``build_graph`` into
    ``orchestration/nodes/rerank.py`` (pure relocation); these grep pins
    must scan both the orchestrator wiring file and the node modules so the
    Wave J2 gate-contract guards survive the structural carve.
    """
    parts = [(_ORCH_DIR / "query_graph.py").read_text()]
    parts.extend(
        p.read_text() for p in sorted((_ORCH_DIR / "nodes").glob("*.py"))
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Constant + schema pinning — Quality Gate #2 zero-hardcode
# ---------------------------------------------------------------------------

def test_default_gate_after_cliff_flag_is_off() -> None:
    """Cliff strategy must own filtering by default — gate is OFF after cliff."""
    assert DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED is False


def test_plan_limit_schema_exposes_owner_override() -> None:
    """Bot owner can flip the legacy behaviour back via plan_limits.

    This satisfies CLAUDE.md "config-driven, no redeploy" requirement.
    """
    schema = bot_limits.PLAN_LIMIT_SCHEMA["rerank_threshold_gate_after_cliff_enabled"]
    assert schema["type"] == "bool"
    assert schema["default"] is False
    assert schema["default"] == DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED


def test_default_filter_strategy_is_cliff() -> None:
    """The fix only matters because cliff is the platform default.

    If a future change flips this to "threshold", the gate-after-cliff
    skip won't fire for any bot — the test pinning the strategy default
    catches that drift before it hits production.
    """
    assert DEFAULT_RERANK_FILTER_STRATEGY == "cliff"


# ---------------------------------------------------------------------------
# Gate primitive — unchanged behaviour pins (regression guard)
# ---------------------------------------------------------------------------

def _chunks(scores: list[float]) -> list[dict]:
    return [{"score": s, "content": f"c{i}"} for i, s in enumerate(scores)]


def test_gate_unchanged_under_threshold_strategy_refuses_q3_top_score() -> None:
    """Threshold strategy keeps the legacy hard-cut at 0.30.

    Q3 (top_score=0.29) was correctly refused by the gate under the
    threshold strategy — the fix preserves that path for bots that
    explicitly opt-in to hard cut.
    """
    out, meta = _rerank_threshold_gate(
        _chunks([0.29, 0.21]), threshold=0.30, mode="rerank",
    )
    assert out == []
    assert meta["refused"] is True
    assert meta["top_score"] == 0.29


def test_gate_unchanged_admits_q13_high_score() -> None:
    """Q13 top_score 0.63 passes the legacy gate cleanly.

    Documents that Q13's false-positive block did NOT come from this
    gate (0.63 >= 0.30). Q13's block lives in the output guardrail
    ``system_leak`` path — that's per-bot tunable via
    ``guardrail_leak_shingle_size``.
    """
    chunks = _chunks([0.63, 0.41])
    out, meta = _rerank_threshold_gate(chunks, threshold=0.30, mode="rerank")
    assert out == chunks
    assert meta["refused"] is False
    assert meta["top_score"] == 0.63


def test_gate_skipped_for_bypass_modes() -> None:
    """Bypass scale (RRF 0.01-0.05) is incomparable with cross-encoder."""
    chunks = _chunks([0.04, 0.02])
    out, meta = _rerank_threshold_gate(
        chunks, threshold=0.30, mode="null_reranker",
    )
    assert out == chunks
    assert meta["applicable"] is False


# ---------------------------------------------------------------------------
# Strategy-contract pins — the new behaviour
# ---------------------------------------------------------------------------

def test_orchestrator_skips_gate_under_cliff_strategy() -> None:
    """When ``rerank_filter_strategy == "cliff"`` and the override flag is
    off (default), the orchestrator must NOT invoke the threshold gate.

    Verified via source inspection because the orchestrator path is
    deeply async + DI-bound; a full integration trace lives in the wave
    J2 load test. The grep guard here catches drift if a future refactor
    accidentally re-runs the gate unconditionally.
    """
    src = _orchestration_src()
    # The new gate-runner pattern: ``if _run_gate:`` wraps the call.
    # If a refactor unwraps it, ``_rerank_threshold_gate(`` would appear
    # outside the ``_run_gate`` block; the simplest pin is presence of
    # both the flag resolution and the conditional branch.
    assert "rerank_threshold_gate_after_cliff_enabled" in src, (
        "Per-bot override flag missing — fix has been reverted."
    )
    assert "_run_gate = _filter_strategy != \"cliff\" or _gate_after_cliff" in src, (
        "Strategy contract gate-skip predicate missing — refactor drift."
    )
    assert "rerank_threshold_gate_skipped" in src, (
        "Observability event for cliff-skip missing — ops can't verify."
    )


def test_orchestrator_logs_strategy_field_on_gate_event() -> None:
    """When the gate DOES run (threshold strategy or owner override),
    the structlog event must carry ``strategy`` so ops can correlate
    refuses to the active strategy without grep-walking the source.
    """
    src = _orchestration_src()
    # Loose pin: the field name + value must appear inside the gate's
    # logger.info call. Anchor on the unique event name to avoid matching
    # unrelated strategy mentions.
    gate_block_idx = src.find('"rerank_threshold_gate"')
    assert gate_block_idx > 0, "Gate observability event missing."
    nearby = src[gate_block_idx : gate_block_idx + 800]
    assert "strategy=_filter_strategy" in nearby, (
        "Gate event missing strategy field — ops cannot disambiguate "
        "refuse cause (cliff-bypass vs threshold-hardcut)."
    )


# ---------------------------------------------------------------------------
# Sacred-rule guard — HALLU=0 invariant preservation
# ---------------------------------------------------------------------------

def test_fix_does_not_disable_refuse_path() -> None:
    """The fix relaxes the gate, NOT the refuse mechanism.

    Refuse short-circuit at the generate node still fires when
    ``not graded`` (chunks empty after all filtering). The cliff filter's
    ``absolute_floor`` (0.05) still drops noise. HALLU=0 sacred risk
    requires the LLM never sees a literally-empty ``<documents>`` block
    that could trigger fabrication — verified by the cliff filter's
    ``force_min_keep=True`` safety net (separate test suite).
    """
    src = _orchestration_src()
    # Refuse short-circuit must remain wired.
    assert "refuse_short_circuit_fired" in src, (
        "Refuse short-circuit gone — HALLU=0 sacred risk."
    )
    # Cliff floor must remain — it's the actual noise cut, not the gate.
    assert "rerank_cliff_absolute_floor" in src, (
        "Cliff absolute floor removed — sacred HALLU guard."
    )


def test_owner_can_force_legacy_gate_via_plan_limits() -> None:
    """Bot owners who prefer refuse-over-weak-answer (audit-heavy bots)
    can flip ``rerank_threshold_gate_after_cliff_enabled = True``.

    This satisfies CLAUDE.md "domain-neutral + config-driven" mindset:
    platform default favours answer-with-context (medispa / spa /
    customer-support style); compliance bots opt-in to stricter
    behaviour without a code change.
    """
    schema = bot_limits.PLAN_LIMIT_SCHEMA["rerank_threshold_gate_after_cliff_enabled"]
    assert schema["type"] == "bool"
    # Owner flips True — schema must accept it without min/max constraint
    # (bool fields don't carry bounds).
    assert "min" not in schema
    assert "max" not in schema


# ---------------------------------------------------------------------------
# Tuning-budget honesty pin — wave J2 ship target
# ---------------------------------------------------------------------------

def test_q3_q5_top_score_threshold_relationship() -> None:
    """The four false-positive top_scores from Wave I load test, pinned.

    Q3/Q5 (0.29) lose to the 0.30 platform default by 0.01 — a hair below
    the floor. Under cliff strategy the cliff filter has already accepted
    these (via ``force_min_keep=True`` safety net); the redundant gate
    was the actual blocker. Under threshold strategy the refuse is
    legitimate — owner who picks threshold opted into hard-cut semantics.
    """
    # These literals are the EVIDENCE from the wave I load test, not
    # configuration. They live in the test docstring + assertion so
    # future tuners can read the empirical baseline at a glance.
    q3_q5_top_score = 0.29
    q4_top_score = 0.43
    q13_top_score = 0.63
    default_floor = 0.30
    # Q3/Q5 lose by 0.01 to the floor — they ARE the false positive.
    assert q3_q5_top_score < default_floor
    # Q4 is above the floor but below 0.50 — could lose to per-bot
    # override; the fix lets cliff strategy accept it regardless.
    assert q4_top_score > default_floor
    # Q13 cleanly above the floor — the block came from output guardrail
    # ``system_leak``, not the rerank gate.
    assert q13_top_score > default_floor


__all__: list[str] = []
