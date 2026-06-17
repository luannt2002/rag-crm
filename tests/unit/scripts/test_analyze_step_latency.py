"""Aggregation + percentile coverage for ``scripts/analyze_step_latency.py``.

MoM 00c-analytics — verify the per-step latency analyzer:
  1. ``aggregate`` groups rows by ``step_name`` and computes p50/p95/p99
     with nearest-rank semantics on the sorted duration list.
  2. ``aggregate`` counts non-``success`` rows toward ``error_rate_pct``.
  3. ``aggregate`` lifts the first non-null ``feature_flag`` per step.
  4. ``render_ascii`` produces a header + data rows sorted by p95 desc.
  5. ``render_ascii`` returns a friendly message on the empty input.
  6. ``build_report`` includes the 4-key identity filter snapshot.

Sacred: tests do not touch the DB; they exercise the pure-Python
aggregation against in-memory fixture rows shaped like
``request_steps`` query output.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_analyzer() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "analyze_step_latency.py"
    assert script_path.exists(), f"analyzer script missing: {script_path}"
    spec = importlib.util.spec_from_file_location(
        "_analyze_step_latency", script_path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def analyzer() -> ModuleType:
    return _load_analyzer()


# ---------------------------------------------------------------------------
# Test 1 — aggregate groups by step_name and computes count + percentiles.
# ---------------------------------------------------------------------------


def test_aggregate_counts_per_step(analyzer: ModuleType) -> None:
    rows = [
        ("retrieve", 100, "success", "ret_flag"),
        ("retrieve", 200, "success", "ret_flag"),
        ("retrieve", 300, "success", "ret_flag"),
        ("rerank", 50, "success", None),
        ("rerank", 80, "success", None),
    ]
    agg = analyzer.aggregate(rows)

    assert set(agg.keys()) == {"retrieve", "rerank"}
    assert agg["retrieve"]["count"] == 3
    assert agg["rerank"]["count"] == 2


# ---------------------------------------------------------------------------
# Test 2 — percentile values follow nearest-rank on sorted durations.
# ---------------------------------------------------------------------------


def test_aggregate_percentile_values(analyzer: ModuleType) -> None:
    # Five durations: [100, 200, 300, 400, 500]. Nearest-rank indices
    # p50 -> 2 -> 300, p95 -> 4 -> 500, p99 -> 4 -> 500.
    rows = [("retrieve", d, "success", None) for d in (500, 100, 300, 200, 400)]
    agg = analyzer.aggregate(rows)

    assert agg["retrieve"]["p50_ms"] == 300
    assert agg["retrieve"]["p95_ms"] == 500
    assert agg["retrieve"]["p99_ms"] == 500


# ---------------------------------------------------------------------------
# Test 3 — non-success status counts toward error_rate.
# ---------------------------------------------------------------------------


def test_aggregate_error_rate(analyzer: ModuleType) -> None:
    rows = [
        ("guardrail_input", 10, "success", None),
        ("guardrail_input", 20, "success", None),
        ("guardrail_input", 30, "error",   None),
        ("guardrail_input", 40, "error",   None),
        ("guardrail_input", 50, "success", None),
    ]
    agg = analyzer.aggregate(rows)

    assert agg["guardrail_input"]["error_count"] == 2
    assert agg["guardrail_input"]["error_rate_pct"] == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# Test 4 — first non-null feature_flag wins per step.
# ---------------------------------------------------------------------------


def test_aggregate_feature_flag_capture(analyzer: ModuleType) -> None:
    rows = [
        ("adapchunk_l3_profile", 5, "success", None),
        ("adapchunk_l3_profile", 7, "success", "adapchunk_layer3_doc_profile_enabled"),
        ("adapchunk_l3_profile", 9, "success", "adapchunk_layer3_doc_profile_enabled"),
    ]
    agg = analyzer.aggregate(rows)
    assert agg["adapchunk_l3_profile"]["feature_flag"] == (
        "adapchunk_layer3_doc_profile_enabled"
    )


# ---------------------------------------------------------------------------
# Test 5 — render_ascii sorts by p95 desc and includes the header.
# ---------------------------------------------------------------------------


def test_render_ascii_sorts_by_p95_desc(analyzer: ModuleType) -> None:
    rows = [
        ("fast_step", 1, "success", None),
        ("fast_step", 2, "success", None),
        ("slow_step", 9000, "success", None),
        ("slow_step", 9000, "success", None),
    ]
    agg = analyzer.aggregate(rows)
    out = analyzer.render_ascii(agg)

    # Header present.
    assert "step_name" in out and "p95_ms" in out
    # slow_step (higher p95) must precede fast_step.
    slow_idx = out.index("slow_step")
    fast_idx = out.index("fast_step")
    assert slow_idx < fast_idx


# ---------------------------------------------------------------------------
# Test 6 — render_ascii returns informative message on empty input.
# ---------------------------------------------------------------------------


def test_render_ascii_empty(analyzer: ModuleType) -> None:
    out = analyzer.render_ascii({})
    assert "no request_steps rows" in out


# ---------------------------------------------------------------------------
# Test 7 — build_report carries the 4-key identity filter snapshot.
# ---------------------------------------------------------------------------


def test_build_report_includes_filter(analyzer: ModuleType) -> None:
    rows = [("retrieve", 100, "success", None)]
    agg = analyzer.aggregate(rows)
    report = analyzer.build_report(
        agg,
        hours=12,
        bot_id="11111111-1111-1111-1111-111111111111",
        record_tenant_id="22222222-2222-2222-2222-222222222222",
        workspace_id="default",
    )
    assert report["schema_version"] == 1
    assert report["window_hours"] == 12
    assert report["step_count"] == 1
    assert report["total_rows"] == 1
    assert report["filter"]["bot_id"] == "11111111-1111-1111-1111-111111111111"
    assert (
        report["filter"]["record_tenant_id"]
        == "22222222-2222-2222-2222-222222222222"
    )
    assert report["filter"]["workspace_id"] == "default"
