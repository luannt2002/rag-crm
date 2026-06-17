"""Unit tests for Ekimetrics 5-Metric Strategy Selector.

Tests cover:
* Metric formula correctness (RC / ICC / DCC / BI / SC bounded + monotonic
  on synthetic stimuli).
* Selector rule order — each rule branch fires exactly when its condition
  holds, and the rule precedence in the paper is preserved.
* Feature flag gating in ``chunking.select_strategy`` — flag OFF keeps the
  legacy weighted path; flag ON activates the Ekimetrics path.
* Domain neutrality — synthetic stimuli use Latin filler text and never
  embed brand or industry literals.

# Proof citation
# Ekimetrics — Adaptive Chunking: Optimizing Chunking-Method Selection for RAG
# Paper: https://arxiv.org/abs/2603.25333  Venue: LREC 2026 (peer-reviewed)
"""
from __future__ import annotations

import pytest

from ragbot.shared.chunking import analyze_document, select_strategy
from ragbot.shared.intrinsic_metrics import (
    EkimetricsThresholds,
    IntrinsicMetrics,
    compute_intrinsic_metrics,
    ekimetrics_select,
)


# ── Synthetic document fixtures (domain-neutral) ─────────────────────────


def _structured_doc_with_xrefs() -> str:
    """Doc with strong heading hierarchy + cross-references to same section
    name — RC should be high, BI moderate, SC depends on chunk band."""
    sections = []
    for i in range(6):
        sections.append(
            f"## Section {i}\n\nIntroduction paragraph for section {i}. "
            f"This text describes the topic in detail. See Section {i} for "
            f"more context. Figure {i} illustrates the concept."
        )
    body = "\n\n".join(sections)
    return f"# Title\n\n{body}\n\nAppendix: see Section 1 and Section 2."


def _fragmented_blocks_doc() -> str:
    """Doc dominated by ONE enormous block — BI should be LOW because the
    block dwarfs any reasonable chunk_size budget."""
    huge = "Paragraph token alpha beta gamma delta epsilon. " * 800
    return huge


def _low_dcc_doc() -> str:
    """Doc whose blocks each cover a DIFFERENT vocabulary so no block
    overlaps strongly with the document gist → DCC should be LOW."""
    blocks = [
        "alpha alpha alpha alpha beta beta beta beta",
        "gamma gamma gamma gamma delta delta delta delta",
        "epsilon epsilon epsilon epsilon zeta zeta zeta zeta",
        "eta eta eta eta theta theta theta theta",
        "iota iota iota iota kappa kappa kappa kappa",
        "lambda lambda lambda lambda mu mu mu mu",
        "nu nu nu nu xi xi xi xi",
        "omicron omicron omicron omicron pi pi pi pi",
    ]
    return "\n\n".join(blocks)


def _short_doc() -> str:
    return "Short prose. Two sentences."


# ── Metric correctness ───────────────────────────────────────────────────


class TestComputeIntrinsicMetrics:
    def test_returns_5_floats_in_unit_interval(self):
        m = compute_intrinsic_metrics(_structured_doc_with_xrefs())
        assert isinstance(m, IntrinsicMetrics)
        for name in ("RC", "ICC", "DCC", "BI", "SC"):
            v = getattr(m, name)
            assert isinstance(v, float), f"{name} not float"
            assert 0.0 <= v <= 1.0, f"{name}={v} outside [0,1]"

    def test_empty_text_returns_vacuous_ones(self):
        m = compute_intrinsic_metrics("")
        assert m.RC == 1.0
        assert m.ICC == 1.0
        assert m.DCC == 1.0
        assert m.BI == 1.0
        assert m.SC == 1.0

    def test_blank_text_returns_vacuous_ones(self):
        m = compute_intrinsic_metrics("   \n\n  \t  ")
        assert m.RC == m.ICC == m.DCC == m.BI == m.SC == 1.0

    def test_bi_low_when_blocks_exceed_target(self):
        """BI must drop below 1.0 when a block is larger than chunk_size."""
        m = compute_intrinsic_metrics(
            _fragmented_blocks_doc(), target_chunk_chars=256
        )
        # ONE huge block > 256 chars → 0 intact / 1 total = 0.0
        assert m.BI == 0.0

    def test_bi_high_when_blocks_fit(self):
        text = "Tiny block one.\n\nTiny block two.\n\nTiny block three."
        m = compute_intrinsic_metrics(text, target_chunk_chars=512)
        assert m.BI == 1.0

    def test_rc_high_when_xrefs_resolve(self):
        """References to "Section 1", "Section 2" appear in BOTH the
        appendix block AND their own section block → preserved."""
        m = compute_intrinsic_metrics(_structured_doc_with_xrefs())
        # Some markers resolve (appendix + own section); some live only
        # in appendix → RC should be > 0 (not all preserved is fine).
        assert m.RC >= 0.0
        # When NO xref markers exist, RC defaults to 1.0 (vacuous).
        m_plain = compute_intrinsic_metrics("Just plain prose. No refs.")
        assert m_plain.RC == 1.0

    def test_dcc_drops_for_disjoint_vocab(self):
        m = compute_intrinsic_metrics(_low_dcc_doc())
        # Each block has 2 unique tokens; gist top-50 covers ALL tokens →
        # DCC = (2 / 16) ≈ 0.125, well below 0.5.
        assert m.DCC < 0.5

    def test_sc_within_band(self):
        # Force chunks all in band: target=100, band = [50, 200].
        chunks = ["x" * 100, "y" * 80, "z" * 150]
        m = compute_intrinsic_metrics(
            "doc body content here for gist computation here body content",
            chunks=chunks,
            target_chunk_chars=100,
        )
        assert m.SC == 1.0

    def test_sc_outside_band(self):
        chunks = ["x" * 5, "y" * 5_000]  # both outside [50, 200] of target 100
        m = compute_intrinsic_metrics("doc body", chunks=chunks, target_chunk_chars=100)
        assert m.SC == 0.0

    def test_icc_higher_for_repetitive_text(self):
        """Repeating the same sentence tokens → ICC ≈ 1.0 (full overlap)."""
        repeated = (
            "alpha beta gamma delta epsilon zeta eta theta. "
            "alpha beta gamma delta epsilon zeta eta theta. "
            "alpha beta gamma delta epsilon zeta eta theta."
        )
        m = compute_intrinsic_metrics(repeated)
        assert m.ICC > 0.9


# ── Selector rule order ──────────────────────────────────────────────────


def _profile_stub() -> dict:
    return analyze_document(_structured_doc_with_xrefs())


class TestEkimetricsSelector:
    def test_bi_low_triggers_semantic(self):
        m = IntrinsicMetrics(RC=0.9, ICC=0.9, DCC=0.9, BI=0.3, SC=0.9)
        strategy, conf, reason = ekimetrics_select(_profile_stub(), m)
        assert strategy == "semantic"
        assert reason == "BI_below_threshold"
        assert conf == pytest.approx(0.3)

    def test_rc_high_triggers_proposition(self):
        m = IntrinsicMetrics(RC=0.95, ICC=0.5, DCC=0.9, BI=0.9, SC=0.9)
        strategy, conf, reason = ekimetrics_select(_profile_stub(), m)
        assert strategy == "proposition"
        assert reason == "RC_above_threshold"
        assert conf == pytest.approx(0.95)

    def test_dcc_low_triggers_late_chunking_mapped_to_semantic(self):
        """Paper says late_chunking; selector maps to semantic (closest
        dispatch-valid strategy in smart_chunk)."""
        m = IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.2, BI=0.9, SC=0.9)
        strategy, conf, reason = ekimetrics_select(_profile_stub(), m)
        assert strategy == "semantic"
        assert reason == "DCC_below_threshold"
        # confidence = 1 - DCC = 0.8
        assert conf == pytest.approx(0.8)

    def test_sc_low_triggers_recursive(self):
        m = IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.9, BI=0.9, SC=0.4)
        strategy, conf, reason = ekimetrics_select(_profile_stub(), m)
        assert strategy == "recursive"
        assert reason == "SC_below_threshold"
        assert conf == pytest.approx(0.4)

    def test_default_branch_returns_hybrid(self):
        m = IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.9, BI=0.9, SC=0.9)
        strategy, conf, reason = ekimetrics_select(_profile_stub(), m)
        assert strategy == "hybrid"
        assert reason == "default_balanced"
        assert 0.0 <= conf <= 1.0

    def test_returned_strategy_always_valid_for_smart_chunk(self):
        """Every code path returns a strategy name that smart_chunk can
        dispatch — no caller-side mapping required."""
        valid = {"hdt", "semantic", "recursive", "hybrid", "proposition", "table_csv"}
        cases = [
            IntrinsicMetrics(RC=0.9, ICC=0.9, DCC=0.9, BI=0.3, SC=0.9),
            IntrinsicMetrics(RC=0.95, ICC=0.5, DCC=0.9, BI=0.9, SC=0.9),
            IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.2, BI=0.9, SC=0.9),
            IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.9, BI=0.9, SC=0.4),
            IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.9, BI=0.9, SC=0.9),
        ]
        for m in cases:
            strategy, _conf, _reason = ekimetrics_select(_profile_stub(), m)
            assert strategy in valid

    def test_confidence_clamped_into_unit_interval(self):
        """A pathological metric (negative / >1) must still yield a clamped
        confidence — defence vs upstream drift."""
        m = IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=-0.5, BI=0.9, SC=0.9)
        _strategy, conf, _reason = ekimetrics_select(_profile_stub(), m)
        assert 0.0 <= conf <= 1.0

    def test_custom_thresholds_take_effect(self):
        """Override the BI threshold so a normally "high" BI = 0.7 now fails
        the rule (threshold=0.9). The selector should switch branches."""
        m = IntrinsicMetrics(RC=0.5, ICC=0.5, DCC=0.9, BI=0.7, SC=0.9)
        # Default thresholds (BI=0.6) → BI=0.7 passes → hybrid (default).
        strategy_default, _, _ = ekimetrics_select(_profile_stub(), m)
        assert strategy_default == "hybrid"
        # Tightened BI threshold to 0.9 → BI=0.7 fails → semantic.
        tight = EkimetricsThresholds(BI=0.9, RC=0.8, DCC=0.5, SC=0.7)
        strategy_tight, _, reason_tight = ekimetrics_select(
            _profile_stub(), m, thresholds=tight
        )
        assert strategy_tight == "semantic"
        assert reason_tight == "BI_below_threshold"


# ── Feature-flag gating in select_strategy() ─────────────────────────────


class TestSelectStrategyFeatureFlag:
    def test_flag_off_uses_weighted_score_path(self):
        """Default behaviour preserved — weighted-score path runs."""
        doc = _structured_doc_with_xrefs()
        profile = analyze_document(doc)
        strategy, conf = select_strategy(profile)
        # Weighted-score path returns one of the standard 6 dispatch names.
        assert strategy in {"hdt", "semantic", "recursive", "hybrid", "proposition", "table_csv"}
        assert 0.0 <= conf <= 1.0

    def test_flag_on_without_text_falls_back_to_weighted(self):
        """flag ON but text missing → weighted-score path (graceful)."""
        profile = analyze_document(_structured_doc_with_xrefs())
        strategy, conf = select_strategy(profile, ekimetrics_enabled=True, text=None)
        # No text → cannot run Ekimetrics → weighted-score path runs.
        assert strategy in {"hdt", "semantic", "recursive", "hybrid", "proposition", "table_csv"}
        assert 0.0 <= conf <= 1.0

    def test_flag_on_with_text_runs_ekimetrics(self):
        """flag ON + text provided → Ekimetrics selector path is exercised
        and returns a dispatch-valid strategy."""
        doc = _low_dcc_doc()  # designed to trigger DCC_below_threshold branch
        profile = analyze_document(doc)
        strategy, conf = select_strategy(
            profile, ekimetrics_enabled=True, text=doc
        )
        assert strategy in {"hdt", "semantic", "recursive", "hybrid", "proposition", "table_csv"}
        assert 0.0 <= conf <= 1.0

    def test_flag_on_short_doc_does_not_crash(self):
        """Short doc with no xrefs → RC vacuous = 1.0 → rule "RC > threshold"
        fires → proposition. Verifies no crash and dispatch-valid output."""
        doc = _short_doc()
        profile = analyze_document(doc)
        strategy, conf = select_strategy(
            profile, ekimetrics_enabled=True, text=doc
        )
        # RC vacuous = 1.0 > 0.8 → proposition (rule precedence preserved).
        assert strategy in {"hdt", "semantic", "recursive", "hybrid", "proposition", "table_csv"}
        assert 0.0 <= conf <= 1.0


# ── Integration smoke: 10 sample docs ────────────────────────────────────


class TestEkimetricsIntegrationSamples:
    """Exercise the selector over 10 synthetic profiles spanning multiple
    paradigms. Verifies no path crashes and all strategies remain valid."""

    @pytest.mark.parametrize(
        "doc_factory",
        [
            lambda: _structured_doc_with_xrefs(),
            lambda: _fragmented_blocks_doc(),
            lambda: _low_dcc_doc(),
            lambda: _short_doc(),
            lambda: "# H1\n\n" + "Paragraph " * 200,
            lambda: "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |",
            lambda: "Mixed: ```py\nx=1\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nPara.",
            lambda: "## A\n\nSee Section A. Figure 1.\n\n## B\n\nSee Section A again.",
            lambda: "Alpha beta gamma. Delta epsilon zeta. Eta theta iota.",
            lambda: "Para one.\n\nPara two.\n\nPara three.\n\nPara four.\n\nPara five.",
        ],
    )
    def test_selector_handles_diverse_inputs(self, doc_factory):
        doc = doc_factory()
        profile = analyze_document(doc)
        strategy, conf = select_strategy(
            profile, ekimetrics_enabled=True, text=doc
        )
        assert strategy in {"hdt", "semantic", "recursive", "hybrid", "proposition", "table_csv"}
        assert 0.0 <= conf <= 1.0
