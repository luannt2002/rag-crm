"""Verdict gate + lift computation coverage for
``scripts/feature_ablation_report.py``.

MoM 00c-analytics — verify the ablation report:
  1. ``decide`` returns DROP_HALLU_BREACH the moment HALLU rises.
  2. ``decide`` returns KEEP_QUALITY_LIFT when ΔPASS hits the threshold.
  3. ``decide`` returns KEEP_LATENCY_WIN / KEEP_COST_WIN on tail metrics.
  4. ``decide`` returns TUNE_MARGINAL for small positive lift.
  5. ``decide`` returns DROP_NO_VALUE when nothing improves.
  6. ``pick_master`` / ``pick_baseline`` auto-detect by hint, then by
     flag-on count.
  7. ``pick_drop_run`` returns the single-flag-different run.
  8. ``compute_per_feature`` skips flags that are OFF in the master.
  9. End-to-end CLI run on a sample input produces a markdown table.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_report() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "feature_ablation_report.py"
    assert script_path.exists(), f"report script missing: {script_path}"
    spec = importlib.util.spec_from_file_location(
        "_feature_ablation_report", script_path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report_mod() -> ModuleType:
    return _load_report()


# ---------------------------------------------------------------------------
# Decision gate tests.
# ---------------------------------------------------------------------------


def test_decide_hallu_breach_wins(report_mod: ModuleType) -> None:
    # Even with massive PASS lift, HALLU breach must DROP.
    v = report_mod.decide(
        delta_pass_pp=20.0,
        delta_hallu=0.01,
        delta_p95_pct=-50.0,
        delta_cost_pct=-80.0,
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    assert v == "DROP_HALLU_BREACH"


def test_decide_quality_lift(report_mod: ModuleType) -> None:
    v = report_mod.decide(
        delta_pass_pp=3.0,
        delta_hallu=0.0,
        delta_p95_pct=0.0,
        delta_cost_pct=0.0,
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    assert v == "KEEP_QUALITY_LIFT"


def test_decide_latency_win(report_mod: ModuleType) -> None:
    v = report_mod.decide(
        delta_pass_pp=0.0,
        delta_hallu=0.0,
        delta_p95_pct=-20.0,   # 20% latency drop
        delta_cost_pct=0.0,
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    assert v == "KEEP_LATENCY_WIN"


def test_decide_cost_win(report_mod: ModuleType) -> None:
    v = report_mod.decide(
        delta_pass_pp=0.0,
        delta_hallu=0.0,
        delta_p95_pct=0.0,
        delta_cost_pct=-25.0,   # 25% cost drop
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    assert v == "KEEP_COST_WIN"


def test_decide_tune_marginal(report_mod: ModuleType) -> None:
    v = report_mod.decide(
        delta_pass_pp=0.5,   # positive but below threshold
        delta_hallu=0.0,
        delta_p95_pct=0.0,
        delta_cost_pct=0.0,
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    assert v == "TUNE_MARGINAL"


def test_decide_drop_no_value(report_mod: ModuleType) -> None:
    v = report_mod.decide(
        delta_pass_pp=-0.5,
        delta_hallu=0.0,
        delta_p95_pct=5.0,    # actually slower
        delta_cost_pct=5.0,   # actually more expensive
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    assert v == "DROP_NO_VALUE"


# ---------------------------------------------------------------------------
# Config selection tests.
# ---------------------------------------------------------------------------


def _runs_minimal() -> list[dict]:
    return [
        {
            "config_name": "A_baseline",
            "pass_rate": 0.85,
            "hallu_rate": 0.0,
            "p95_ms": 22000,
            "cost_per_turn": 0.012,
            "feature_flags": {"f1": False, "f2": False},
        },
        {
            "config_name": "D_master_all_on",
            "pass_rate": 0.93,
            "hallu_rate": 0.0,
            "p95_ms": 13000,
            "cost_per_turn": 0.008,
            "feature_flags": {"f1": True, "f2": True},
        },
    ]


def test_pick_master_by_hint(report_mod: ModuleType) -> None:
    runs = _runs_minimal()
    m = report_mod.pick_master(runs, None)
    assert m["config_name"] == "D_master_all_on"


def test_pick_baseline_by_hint(report_mod: ModuleType) -> None:
    runs = _runs_minimal()
    b = report_mod.pick_baseline(runs, None)
    assert b["config_name"] == "A_baseline"


def test_pick_master_fallback_by_flag_count(report_mod: ModuleType) -> None:
    # No hint match — picker falls back to max ON flags.
    runs = [
        {
            "config_name": "alpha",
            "pass_rate": 0.5, "hallu_rate": 0.0, "p95_ms": 10000,
            "cost_per_turn": 0.01,
            "feature_flags": {"a": False, "b": False},
        },
        {
            "config_name": "omega",
            "pass_rate": 0.7, "hallu_rate": 0.0, "p95_ms": 10000,
            "cost_per_turn": 0.01,
            "feature_flags": {"a": True, "b": True},
        },
    ]
    m = report_mod.pick_master(runs, None)
    assert m["config_name"] == "omega"


def test_pick_drop_run_finds_single_diff(report_mod: ModuleType) -> None:
    master = {
        "config_name": "all_on",
        "feature_flags": {"f1": True, "f2": True},
    }
    drop_run = {
        "config_name": "minus_f1",
        "feature_flags": {"f1": False, "f2": True},
    }
    runs = [master, drop_run]
    found = report_mod.pick_drop_run(runs, master, "f1")
    assert found is drop_run


def test_pick_drop_run_returns_none_when_multi_diff(
    report_mod: ModuleType,
) -> None:
    master = {
        "config_name": "all_on",
        "feature_flags": {"f1": True, "f2": True},
    }
    multi_drop = {
        "config_name": "minus_both",
        "feature_flags": {"f1": False, "f2": False},  # two flags differ
    }
    runs = [master, multi_drop]
    found = report_mod.pick_drop_run(runs, master, "f1")
    assert found is None


# ---------------------------------------------------------------------------
# Aggregation skips OFF-in-master flags.
# ---------------------------------------------------------------------------


def test_compute_per_feature_skips_off_in_master(
    report_mod: ModuleType,
) -> None:
    runs = [
        {
            "config_name": "A_baseline",
            "pass_rate": 0.85, "hallu_rate": 0.0, "p95_ms": 22000,
            "cost_per_turn": 0.012,
            "feature_flags": {"f1": False, "f2": False},
        },
        {
            "config_name": "master",
            "pass_rate": 0.90, "hallu_rate": 0.0, "p95_ms": 20000,
            "cost_per_turn": 0.011,
            "feature_flags": {"f1": True, "f2": False},   # only f1 ON
        },
    ]
    rows = report_mod.compute_per_feature(
        runs,
        runs[1],
        runs[0],
        pass_lift_threshold_pp=2.0,
        latency_drop_threshold_pct=10.0,
        cost_drop_threshold_pct=10.0,
    )
    flags = {r["feature_flag"] for r in rows}
    assert flags == {"f1"}  # f2 OFF in master → skipped


# ---------------------------------------------------------------------------
# End-to-end CLI smoke test.
# ---------------------------------------------------------------------------


def test_cli_smoke_runs_and_emits_markdown(tmp_path: Path) -> None:
    runs = [
        {
            "config_name": "A_baseline",
            "pass_rate": 0.85, "hallu_rate": 0.0, "p95_ms": 22000,
            "cost_per_turn": 0.012,
            "feature_flags": {"f1": False},
        },
        {
            "config_name": "D_master_all_on",
            "pass_rate": 0.93, "hallu_rate": 0.0, "p95_ms": 13000,
            "cost_per_turn": 0.008,
            "feature_flags": {"f1": True},
        },
    ]
    input_path = tmp_path / "runs.json"
    input_path.write_text(json.dumps(runs), encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "feature_ablation_report.py"
    result = subprocess.run(
        [sys.executable, str(script), "--input", str(input_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "# Feature Ablation Report" in out
    assert "`f1`" in out
    assert "KEEP_QUALITY_LIFT" in out
