"""Smoke tests for scripts/eval_diff.py + scripts/extract_harness_fails.py.

These scripts live under scripts/ (not a package), so we load them via
sys.path injection rather than package import.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import eval_diff  # type: ignore  # noqa: E402
import extract_harness_fails  # type: ignore  # noqa: E402


def _make_run(
    rooms: list[dict] | None = None,
    *,
    answered_count: int = 3,
    fail_count: int = 1,
) -> dict:
    if rooms is not None:
        return {"rooms": rooms}
    turns: list[dict] = []
    for i in range(answered_count):
        turns.append(
            {
                "answer": f"answered-{i}",
                "answer_type": "answered",
                "answer_reason": "Generated from retrieved context",
                "chunks_used": 2,
                "top_score": 0.10 + i * 0.01,
                "duration_ms": 5000 + i * 100,
                "cost_usd": 0.005,
                "sources": [f"doc-{i}", f"doc-{i}-b"],
                "tokens": {"prompt": 1000, "completion": 20, "cached": 500},
                "debug": {"intent": "factoid", "model": "m1", "source": "query_graph"},
                "_idx": i,
                "_question": f"q-{i}",
            }
        )
    for j in range(fail_count):
        turns.append(
            {
                "answer": "",
                "answer_type": "out_of_scope",
                "answer_reason": "Query classified as out of scope",
                "chunks_used": 0,
                "top_score": 0.0,
                "duration_ms": 800,
                "cost_usd": 0.0,
                "sources": [],
                "tokens": {"prompt": 200, "completion": 0, "cached": 0},
                "debug": {"intent": "chitchat", "model": "m1", "source": "query_graph"},
                "_idx": answered_count + j,
                "_question": f"fail-q-{j}",
            }
        )
    return {
        "rooms": [
            {"room_id": "r01-alpha", "topic": "pricing", "n_turns": len(turns), "turns": turns}
        ]
    }


# ---------------------------- eval_diff ----------------------------


def test_eval_diff_bucketed_stats_produces_numeric_values():
    run = _make_run(answered_count=3, fail_count=1)
    stats = eval_diff._bucketed_stats(run, "room")
    assert "r01-alpha" in stats
    row = stats["r01-alpha"]
    assert row["total_turns"] == 4
    assert row["answered_turns"] == 3
    assert row["answered_rate"] == pytest.approx(0.75)
    assert row["avg_top_score"] > 0.0
    assert row["avg_latency_ms"] > 0.0
    assert row["cost_per_answered"] == pytest.approx(0.005, rel=1e-6)


def test_eval_diff_overall_row_computed():
    base = _make_run(answered_count=4, fail_count=0)
    curr = _make_run(answered_count=3, fail_count=1)
    base_overall = eval_diff._overall(base)
    curr_overall = eval_diff._overall(curr)
    assert base_overall["answered_rate"] == pytest.approx(1.0)
    assert curr_overall["answered_rate"] == pytest.approx(0.75)
    assert base_overall["total_turns"] == 4
    assert curr_overall["total_turns"] == 4


def test_eval_diff_bucket_by_category_uses_topic():
    run = _make_run(answered_count=2, fail_count=1)
    stats = eval_diff._bucketed_stats(run, "category")
    # topic of our synthetic room is "pricing"
    assert "pricing" in stats
    assert stats["pricing"]["total_turns"] == 3


def test_eval_diff_print_delta_table_smoke(capsys):
    base = _make_run(answered_count=3, fail_count=0)
    curr = _make_run(answered_count=2, fail_count=1)
    bs = eval_diff._bucketed_stats(base, "room")
    cs = eval_diff._bucketed_stats(curr, "room")
    bo = eval_diff._overall(base)
    co = eval_diff._overall(curr)
    buf = io.StringIO()
    with redirect_stdout(buf):
        eval_diff._print_delta_table(bs, cs, bo, co, "room")
    out = buf.getvalue()
    assert "OVERALL" in out
    assert "r01-alpha" in out


def test_eval_diff_missing_baseline_file_raises(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit):
        eval_diff._load(missing)


# -------------------------- extract_harness_fails --------------------------


def test_extract_fails_filters_answered_true():
    run = _make_run(answered_count=5, fail_count=2)
    fails = extract_harness_fails._collect_fails(run)
    assert len(fails) == 2
    for f in fails:
        assert f["turn"]["answer_type"] != "answered"


def test_extract_fails_none_when_all_answered():
    run = _make_run(answered_count=3, fail_count=0)
    fails = extract_harness_fails._collect_fails(run)
    assert fails == []


def test_extract_fails_formats_multiline_block():
    run = _make_run(answered_count=1, fail_count=1)
    fails = extract_harness_fails._collect_fails(run)
    block = extract_harness_fails._format_fail(1, 1, fails[0])
    # multi-line and includes all key labels
    assert "\n" in block
    for label in ("prompt:", "answer:", "retrieval:", "timing:"):
        assert label in block
    assert "fail 1/1" in block
    assert "r01-alpha" in block


def test_extract_fails_truncate_handles_none():
    assert extract_harness_fails._truncate(None) == ""
    assert extract_harness_fails._truncate("x" * 10, n=5) == "xx..."


def test_extract_fails_missing_file_raises(tmp_path):
    with pytest.raises(SystemExit):
        extract_harness_fails._load(tmp_path / "absent.json")
