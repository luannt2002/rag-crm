"""Unit coverage for ``scripts/loadtest_perbatch_deepdive.py``.

Per CLAUDE.md, the analyser is pure tooling — it never invokes the LLM,
never injects text into prompts, never overrides answers. Tests verify
the public surface (failure-mode classification, per-batch summary,
trend rollup) on synthetic JSON-shape inputs.

Domain-neutral: every fixture uses placeholder ``q###``/``ans-###``
strings, no brand or industry literal.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Module loader — the analyser lives under scripts/ which is not a Python
# package; load it by file path so tests run regardless of cwd.
# ---------------------------------------------------------------------------


def _load_analyzer() -> Any:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    script = repo_root / "scripts" / "loadtest_perbatch_deepdive.py"
    spec = importlib.util.spec_from_file_location("loadtest_perbatch_deepdive", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register so importlib.reload(mod) works in coverage tests.
    import sys as _sys
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def analyzer() -> Any:
    return _load_analyzer()


# ---------------------------------------------------------------------------
# Synthetic turn factory — no brand / domain literal.
# ---------------------------------------------------------------------------


def _turn(
    *,
    room: int,
    idx: int,
    classification: str,
    chunks_used: int = 0,
    top_score: float = 0.0,
    duration_ms: int = 1_000,
    cost_usd: float = 0.0001,
    question: str | None = None,
    answer: str = "",
    intent: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    t: dict[str, Any] = {
        "room": room,
        "idx": idx,
        "classification": classification,
        "chunks_used": chunks_used,
        "top_score": top_score,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "question": question or f"q-{room:02d}-{idx:02d}",
        "answer": answer or f"ans-{room:02d}-{idx:02d}",
        "tokens_in": 100,
        "tokens_out": 20,
        "cached_tokens": 0,
    }
    if intent is not None:
        t["intent"] = intent
    if error is not None:
        t["error"] = error
    return t


def _three_batch_fixture() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """3 batches × 10 turns with a known mix.

    Batch 1: 10x PASS, all fast.
    Batch 2: 6x PASS + 3x REFUSE_NO_DOCS (chunks=0) + 1x REFUSE_NO_DOCS (chunks>0, low top_score) → CORPUS_GAP*3 + RETRIEVAL_WEAK*1
    Batch 3: 5x PASS + 2x ERROR + 1x EMPTY_FAIL + 1x REFUSE_NO_DOCS slow + 1x PASS slow
        → STREAM_ERROR*2, EMPTY_GENERATE*1, CORPUS_GAP*1 (REFUSE_NO_DOCS chunks=0),
          LATENCY_OUTLIER*2 (one slow non-PASS + one slow PASS)
    """
    turns: list[dict[str, Any]] = []
    # Batch 1 — all PASS, low latency, top_score reasonable
    for i in range(10):
        turns.append(_turn(room=1, idx=i, classification="PASS", chunks_used=3, top_score=0.4))
    # Batch 2
    for i in range(6):
        turns.append(_turn(room=2, idx=i, classification="PASS", chunks_used=2, top_score=0.3))
    for i in range(6, 9):
        turns.append(
            _turn(room=2, idx=i, classification="REFUSE_NO_DOCS", chunks_used=0, top_score=0.0)
        )
    turns.append(
        _turn(room=2, idx=9, classification="REFUSE_NO_DOCS", chunks_used=2, top_score=0.01)
    )
    # Batch 3
    for i in range(5):
        turns.append(_turn(room=3, idx=i, classification="PASS", chunks_used=2, top_score=0.25))
    turns.append(
        _turn(room=3, idx=5, classification="ERROR", chunks_used=0, error="HTTP 500")
    )
    turns.append(
        _turn(room=3, idx=6, classification="ERROR", chunks_used=0, error="HTTP 500")
    )
    turns.append(_turn(room=3, idx=7, classification="EMPTY_FAIL", chunks_used=1, top_score=0.2))
    turns.append(
        _turn(
            room=3,
            idx=8,
            classification="REFUSE_NO_DOCS",
            chunks_used=0,
            duration_ms=20_000,
        )
    )
    turns.append(
        _turn(
            room=3,
            idx=9,
            classification="PASS",
            chunks_used=2,
            top_score=0.5,
            duration_ms=18_000,
        )
    )
    config = {
        "bot_id": "bot-test",
        "tenant_id": 999,
        "channel_type": "web",
        "rooms": [1, 2, 3],
    }
    return turns, config


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_per_batch_counts_correct(analyzer: Any) -> None:
    turns, _cfg = _three_batch_fixture()
    batches = analyzer.slice_turns(turns, batch_size=10)
    assert len(batches) == 3
    s1 = analyzer.summarize_batch(batches[0][2])
    s2 = analyzer.summarize_batch(batches[1][2])
    s3 = analyzer.summarize_batch(batches[2][2])
    assert s1["counts"] == {"PASS": 10}
    assert s2["counts"] == {"PASS": 6, "REFUSE_NO_DOCS": 4}
    assert s3["counts"] == {
        "PASS": 6,
        "ERROR": 2,
        "EMPTY_FAIL": 1,
        "REFUSE_NO_DOCS": 1,
    }


def test_failure_mode_classification_corpus_gap(analyzer: Any) -> None:
    t = {
        "classification": "REFUSE_NO_DOCS",
        "chunks_used": 0,
        "top_score": 0.0,
        "duration_ms": 1_000,
    }
    modes = analyzer.classify_failure_modes(t)
    assert "CORPUS_GAP" in modes
    assert "RETRIEVAL_WEAK" not in modes
    assert "LATENCY_OUTLIER" not in modes


def test_failure_mode_classification_retrieval_weak(analyzer: Any) -> None:
    t = {
        "classification": "REFUSE_NO_DOCS",
        "chunks_used": 5,
        "top_score": 0.01,  # below RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD = 0.05
        "duration_ms": 1_000,
    }
    modes = analyzer.classify_failure_modes(t)
    assert "RETRIEVAL_WEAK" in modes
    assert "CORPUS_GAP" not in modes


def test_failure_mode_classification_latency_outlier(analyzer: Any) -> None:
    """LATENCY_OUTLIER should fire even on PASS turns when duration is huge."""
    t = {
        "classification": "PASS",
        "chunks_used": 3,
        "top_score": 0.5,
        "duration_ms": 30_000,  # > LATENCY_OUTLIER_DURATION_MS = 15_000
    }
    modes = analyzer.classify_failure_modes(t)
    assert modes == ["LATENCY_OUTLIER"]


def test_failure_mode_classification_stream_error_and_empty(analyzer: Any) -> None:
    err_modes = analyzer.classify_failure_modes(
        {"classification": "ERROR", "chunks_used": 0}
    )
    assert "STREAM_ERROR" in err_modes
    assert "CORPUS_GAP" in err_modes  # ERROR + chunks=0 also tags CORPUS_GAP
    empty_modes = analyzer.classify_failure_modes(
        {"classification": "EMPTY_FAIL", "chunks_used": 1, "top_score": 0.3}
    )
    assert empty_modes == ["EMPTY_GENERATE"]


def test_aggregate_summary_matches_sum_of_batches(analyzer: Any) -> None:
    turns, _cfg = _three_batch_fixture()
    batches = analyzer.slice_turns(turns, batch_size=10)
    summaries = [analyzer.summarize_batch(sub) for _lo, _hi, sub in batches]
    # PASS sum across batches matches direct count over all turns.
    pass_sum = sum(s["counts"].get("PASS", 0) for s in summaries)
    pass_direct = sum(1 for t in turns if t["classification"] == "PASS")
    # Batch1 PASS=10 + Batch2 PASS=6 + Batch3 PASS=6 (5 fast + 1 slow) = 22.
    assert pass_sum == pass_direct == 22
    # Cost rolls up.
    cost_sum = round(sum(s["cost_usd_total"] for s in summaries), 6)
    cost_direct = round(sum(t["cost_usd"] for t in turns), 6)
    assert cost_sum == cost_direct
    # Failure mode portfolio sum.
    fm_total: dict[str, int] = {}
    for s in summaries:
        for mode, n in s["failure_modes"].items():
            fm_total[mode] = fm_total.get(mode, 0) + n
    # 4 CORPUS_GAP from b2 (3 chunks=0) + 1 RETRIEVAL_WEAK from b2 + 1 CORPUS_GAP
    # from b3 REFUSE_NO_DOCS chunks=0 + 2 STREAM_ERROR + 2 CORPUS_GAP from those
    # ERROR turns (chunks=0 + cls != PASS) + 1 EMPTY_GENERATE + 2 LATENCY_OUTLIER.
    assert fm_total["CORPUS_GAP"] == 6
    assert fm_total["RETRIEVAL_WEAK"] == 1
    assert fm_total["STREAM_ERROR"] == 2
    assert fm_total["EMPTY_GENERATE"] == 1
    assert fm_total["LATENCY_OUTLIER"] == 2


def test_build_report_renders_all_sections(analyzer: Any, tmp_path: Path) -> None:
    turns, cfg = _three_batch_fixture()
    md = analyzer.build_report(
        source_label="synthetic.json",
        config_block=cfg,
        turns=turns,
        batch_size=10,
    )
    # Top-level title
    assert md.startswith("# Per-batch deep-dive — synthetic.json")
    # All 5 mandatory sections present
    for header in (
        "## Source config",
        "## Thresholds in use",
        "## Per-batch overview",
        "## Per-batch failure-mode breakdown",
        "## Per-batch worst questions",
        "## Latency progression",
        "## Cumulative cost",
    ):
        assert header in md, f"missing section header {header}"
    # Failure mode column headers
    for mode in ("CORPUS_GAP", "RETRIEVAL_WEAK", "LATENCY_OUTLIER", "STREAM_ERROR", "EMPTY_GENERATE"):
        assert mode in md


def test_intent_breakdown_emitted_only_when_intent_present(analyzer: Any) -> None:
    turns, cfg = _three_batch_fixture()
    md_no_intent = analyzer.build_report(
        source_label="x.json", config_block=cfg, turns=turns, batch_size=10
    )
    assert "## Per-intent breakdown" not in md_no_intent
    # Now annotate intents on a few turns.
    turns_with_intent = [dict(t) for t in turns]
    for i, t in enumerate(turns_with_intent):
        t["intent"] = "intent_a" if i % 2 == 0 else "intent_b"
    md_intent = analyzer.build_report(
        source_label="x.json", config_block=cfg, turns=turns_with_intent, batch_size=10
    )
    assert "## Per-intent breakdown" in md_intent
    assert "intent_a" in md_intent
    assert "intent_b" in md_intent


def test_aggregate_load_and_trend_render(analyzer: Any, tmp_path: Path) -> None:
    """End-to-end: write a fake aggregate JSON, run trend across 2 fakes."""
    turns, cfg = _three_batch_fixture()
    payload_a = {"config": cfg, "turns": turns, "summary": {}}
    payload_b = {"config": cfg, "turns": turns[:15], "summary": {}}
    fa = tmp_path / "round_a.json"
    fb = tmp_path / "round_b.json"
    fa.write_text(json.dumps(payload_a), encoding="utf-8")
    fb.write_text(json.dumps(payload_b), encoding="utf-8")

    cfg_loaded, turns_loaded = analyzer.load_aggregate(fa)
    assert cfg_loaded["bot_id"] == "bot-test"
    assert len(turns_loaded) == 30

    md_trend = analyzer.render_trend([fa, fb])
    assert md_trend.startswith("# Cross-round trend")
    assert "round_a.json" in md_trend
    assert "round_b.json" in md_trend


def test_load_batch_glob_orders_by_idx(analyzer: Any, tmp_path: Path) -> None:
    """Glob loader must concatenate batch_NN.json in numeric idx order."""
    turns, cfg = _three_batch_fixture()
    # Write 3 batch checkpoints (idx 1..3, 10 turns each) in REVERSE order.
    for idx, lo in [(3, 21), (1, 1), (2, 11)]:
        sub = turns[lo - 1 : lo - 1 + 10]
        payload = {
            "config": cfg,
            "batch": {"idx": idx, "total": 3, "turn_range": [lo, lo + 9]},
            "summary": {},
            "turns": sub,
        }
        (tmp_path / f"out.batch_{idx:02d}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    cfg_loaded, all_turns, declared = analyzer.load_batch_glob(
        str(tmp_path / "out.batch_*.json")
    )
    assert cfg_loaded["bot_id"] == "bot-test"
    assert len(all_turns) == 30
    # Check ordering — first turn is room=1, idx=0 (from batch 1).
    assert all_turns[0]["room"] == 1 and all_turns[0]["idx"] == 0
    # Last turn is room=3, idx=9 (from batch 3).
    assert all_turns[-1]["room"] == 3 and all_turns[-1]["idx"] == 9
    assert declared == 10


def test_slice_turns_rejects_zero_or_negative(analyzer: Any) -> None:
    with pytest.raises(ValueError):
        analyzer.slice_turns([{"x": 1}], batch_size=0)
    with pytest.raises(ValueError):
        analyzer.slice_turns([{"x": 1}], batch_size=-3)


def test_percentile_edge_cases(analyzer: Any) -> None:
    # Empty list → 0.0
    assert analyzer._percentile([], 50) == 0.0
    # Single value
    assert analyzer._percentile([42.0], 50) == 42.0
    assert analyzer._percentile([42.0], 99) == 42.0
    # Clamps
    assert analyzer._percentile([1.0, 2.0, 3.0], 0) == 1.0
    assert analyzer._percentile([1.0, 2.0, 3.0], 100) == 3.0
    # Median of evenly-spaced values
    assert analyzer._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0


def test_domain_neutral_input_no_brand_leak(analyzer: Any) -> None:
    """Fixture must contain zero brand / industry / customer literal."""
    turns, cfg = _three_batch_fixture()
    md = analyzer.build_report(
        source_label="x.json", config_block=cfg, turns=turns, batch_size=10
    )
    blacklist = (
        "spa", "massage", "chăm sóc da", "triệt lông", "gội đầu",
        "<known-brand>", "innocom",
    )
    md_lc = md.lower()
    for term in blacklist:
        assert term.lower() not in md_lc, f"domain literal leaked into report: {term!r}"


def test_module_imports_cleanly() -> None:
    """Loading the analyser twice must produce identical constants and a
    fresh module object, proving zero global side-effects on import."""
    mod1 = _load_analyzer()
    mod2 = _load_analyzer()
    assert mod1.RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD == mod2.RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD
    assert mod1.LATENCY_OUTLIER_DURATION_MS == mod2.LATENCY_OUTLIER_DURATION_MS
    assert mod1.FAILURE_MODES == mod2.FAILURE_MODES
    assert mod1.BUCKETS == mod2.BUCKETS
