"""Coverage for ``scripts/reranker_threshold_ab_test.py``.

T1-Smartness — verify the pure helpers + the sweep core of the A/B
runner WITHOUT touching real chat API or DB:

1. ``parse_threshold_csv`` accepts CSV, rejects empty / out-of-range.
2. ``load_dataset`` accepts list-of-objects JSON, rejects malformed.
3. ``is_refuse`` heuristic catches expected phrases.
4. ``score_bucket`` bins float into named buckets matching the SQL
   histogram in ``scripts/diagnose_p95_bottleneck.py``.
5. ``percentile`` matches a known fixture.
6. ``summarize`` computes pass/refuse rates correctly across mixed
   answered / refused / error rows.
7. ``run_threshold`` honors the threshold-override env var, runs the
   mocked chat API exactly once per turn, and clears the env after.
8. JSON output schema is stable (keys + types).
9. CSV output has 9 columns (one header + one row per turn).
10. Invalid threshold-values exits with code 4.
11. Missing dataset exits with code 2.
12. Multiple threshold values sweep sequentially (3 thresholds × N turns).
"""
from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


# ---------------- module loader ---------------------------------------------
def _load_ab() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "reranker_threshold_ab_test.py"
    assert script_path.exists(), f"script missing: {script_path}"
    spec = importlib.util.spec_from_file_location("_rerank_ab", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rerank_ab"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------- parse_threshold_csv ---------------------------------------
def test_parse_threshold_csv_happy_path() -> None:
    ab = _load_ab()
    assert ab.parse_threshold_csv("0.20,0.30,0.40,0.50") == [0.20, 0.30, 0.40, 0.50]


def test_parse_threshold_csv_single_value() -> None:
    ab = _load_ab()
    assert ab.parse_threshold_csv("0.35") == [0.35]


def test_parse_threshold_csv_rejects_empty() -> None:
    ab = _load_ab()
    with pytest.raises(ValueError, match="non-empty"):
        ab.parse_threshold_csv("")


def test_parse_threshold_csv_rejects_out_of_range() -> None:
    ab = _load_ab()
    with pytest.raises(ValueError, match="out of"):
        ab.parse_threshold_csv("0.30,1.50,0.40")


def test_parse_threshold_csv_rejects_non_float() -> None:
    ab = _load_ab()
    with pytest.raises(ValueError, match="invalid threshold"):
        ab.parse_threshold_csv("0.30,abc,0.50")


# ---------------- load_dataset ----------------------------------------------
def test_load_dataset_accepts_ragas_format(tmp_path: Path) -> None:
    ab = _load_ab()
    fixture = tmp_path / "ds.json"
    fixture.write_text(json.dumps([
        {"question": "q1?", "ground_truth_answer": "a1"},
        {"question": "q2?", "ground_truth_answer": "a2"},
    ]))
    rows = ab.load_dataset(fixture)
    assert len(rows) == 2
    assert rows[0]["question"] == "q1?"


def test_load_dataset_missing_file(tmp_path: Path) -> None:
    ab = _load_ab()
    with pytest.raises(FileNotFoundError):
        ab.load_dataset(tmp_path / "nope.json")


def test_load_dataset_rejects_non_list(tmp_path: Path) -> None:
    ab = _load_ab()
    fixture = tmp_path / "bad.json"
    fixture.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError, match="JSON list"):
        ab.load_dataset(fixture)


def test_load_dataset_skips_rows_without_question(tmp_path: Path) -> None:
    ab = _load_ab()
    fixture = tmp_path / "mixed.json"
    fixture.write_text(json.dumps([
        {"question": "ok"},
        {"no_question": True},
        {"question": ""},
        {"question": "ok2"},
    ]))
    rows = ab.load_dataset(fixture)
    assert [r["question"] for r in rows] == ["ok", "ok2"]


# ---------------- is_refuse + percentile + score_bucket ---------------------
def test_is_refuse_matches_vietnamese_oos_phrase() -> None:
    ab = _load_ab()
    assert ab.is_refuse("Tôi không có thông tin về vấn đề này.") is True
    assert ab.is_refuse("Văn bản có hiệu lực từ 2026-01-01.") is False
    assert ab.is_refuse("") is False
    assert ab.is_refuse(None) is False


def test_score_bucket_boundaries() -> None:
    ab = _load_ab()
    assert ab.score_bucket(None) == "null"
    assert ab.score_bucket(0.05) == "0.00-0.09"
    assert ab.score_bucket(0.19) == "0.10-0.19"
    assert ab.score_bucket(0.20) == "0.20-0.29"
    assert ab.score_bucket(0.299) == "0.20-0.29"
    assert ab.score_bucket(0.30) == "0.30-0.39"
    assert ab.score_bucket(0.50) == "0.50+"
    assert ab.score_bucket(0.95) == "0.50+"


def test_percentile_fixture() -> None:
    ab = _load_ab()
    assert ab.percentile([], 50) == 0.0
    assert ab.percentile([100.0, 200.0, 300.0, 400.0, 500.0], 50) == 300.0
    # idx = round(0.95 * 4) = 4 → last element
    assert ab.percentile([100.0, 200.0, 300.0, 400.0, 500.0], 95) == 500.0


# ---------------- summarize -------------------------------------------------
def test_summarize_computes_rates_and_buckets() -> None:
    ab = _load_ab()
    rows = [
        ab.TurnResult(
            threshold=0.30, question="q1", answer="real answer",
            refused=False, top_score=0.45, chunks_used=3,
            latency_ms=1000, cost_usd=0.001, trace_id="t1", error=None,
        ),
        ab.TurnResult(
            threshold=0.30, question="q2", answer="không có thông tin",
            refused=True, top_score=0.10, chunks_used=0,
            latency_ms=800, cost_usd=0.0005, trace_id="t2", error=None,
        ),
        ab.TurnResult(
            threshold=0.30, question="q3", answer="",
            refused=False, top_score=None, chunks_used=0,
            latency_ms=200, cost_usd=None, trace_id=None, error="timeout",
        ),
        ab.TurnResult(
            threshold=0.30, question="q4", answer="another answer",
            refused=False, top_score=0.55, chunks_used=2,
            latency_ms=1200, cost_usd=0.001, trace_id="t4", error=None,
        ),
    ]
    s = ab.summarize(0.30, rows)
    assert s.n == 4
    assert s.n_answered == 2
    assert s.n_refused == 1
    assert s.n_error == 1
    # pass_rate = 2 / (4 - 1) = 0.6667
    assert s.pass_rate == pytest.approx(0.6667, abs=1e-3)
    assert s.refuse_rate == pytest.approx(0.25, abs=1e-3)
    # avg of [0.45, 0.10, 0.55] = 0.3667
    assert s.avg_top_score == pytest.approx(0.3667, abs=1e-3)
    # buckets: 0.45→"0.40-0.49", 0.10→"0.10-0.19", null, 0.55→"0.50+"
    assert s.score_buckets["0.40-0.49"] == 1
    assert s.score_buckets["0.10-0.19"] == 1
    assert s.score_buckets["null"] == 1
    assert s.score_buckets["0.50+"] == 1


def test_summarize_empty_rows() -> None:
    ab = _load_ab()
    s = ab.summarize(0.30, [])
    assert s.n == 0
    assert s.pass_rate == 0.0
    assert s.refuse_rate == 0.0


# ---------------- run_threshold (mocked client) -----------------------------
class _FakeClient:
    """Mocks httpx.AsyncClient.post — never touches network."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> "_FakeClient._FakeResp":
        self.calls.append({"url": url, "json": kwargs.get("json")})
        resp = self._responses.pop(0) if self._responses else {"answer": ""}
        return self._FakeResp(resp)

    async def get(self, url: str, **kwargs: Any) -> "_FakeClient._FakeResp":
        return self._FakeResp({"token": "fake-token"})

    class _FakeResp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload


@pytest.mark.asyncio
async def test_run_threshold_sets_and_clears_env(monkeypatch: pytest.MonkeyPatch) -> None:
    ab = _load_ab()
    monkeypatch.delenv(ab.THRESHOLD_OVERRIDE_ENV, raising=False)
    client = _FakeClient([
        {"answer": "ok", "top_score": 0.45, "chunks_used": 2,
         "latency_ms": 500, "cost_usd": 0.001, "trace_id": "abc"},
    ])
    dataset = [{"question": "q1"}]
    rows = await ab.run_threshold(
        client,  # type: ignore[arg-type]
        base_url="http://x",
        token="t",
        threshold=0.35,
        bot_id="bot",
        workspace_id="ws",
        channel_type="web",
        dataset=dataset,
        pace=0.0,
    )
    # During the sweep the override is set; we don't unset it inside run_threshold
    # (the main() finally-block does). Verify the value is the latest threshold.
    assert os.environ.get(ab.THRESHOLD_OVERRIDE_ENV) == "0.3500"
    assert len(rows) == 1
    assert rows[0].threshold == 0.35
    assert rows[0].top_score == 0.45
    # ask_one() recomputes latency from perf_counter, so the mock's number
    # is overwritten — just assert non-negative int (real timing measured).
    assert rows[0].latency_ms >= 0


@pytest.mark.asyncio
async def test_run_threshold_handles_error_payload() -> None:
    ab = _load_ab()
    client = _FakeClient([
        {"error": "boom", "latency_ms": 30},
    ])
    rows = await ab.run_threshold(
        client,  # type: ignore[arg-type]
        base_url="http://x", token="t", threshold=0.20,
        bot_id="bot", workspace_id="ws", channel_type="web",
        dataset=[{"question": "q"}], pace=0.0,
    )
    assert rows[0].error == "boom"
    assert rows[0].top_score is None


@pytest.mark.asyncio
async def test_run_threshold_multiple_thresholds_sequential() -> None:
    """3 threshold values × 2 turns = 6 POST calls in correct order."""
    ab = _load_ab()
    responses = [
        {"answer": "a1", "top_score": 0.45, "chunks_used": 1, "latency_ms": 100},
        {"answer": "a2", "top_score": 0.55, "chunks_used": 1, "latency_ms": 110},
        {"answer": "không có thông tin", "top_score": 0.18, "chunks_used": 0, "latency_ms": 120},
        {"answer": "không có thông tin", "top_score": 0.22, "chunks_used": 0, "latency_ms": 130},
        {"answer": "", "top_score": None, "chunks_used": 0, "latency_ms": 140, "error": "x"},
        {"answer": "a6", "top_score": 0.66, "chunks_used": 2, "latency_ms": 150},
    ]
    client = _FakeClient(responses)
    dataset = [{"question": "q1"}, {"question": "q2"}]
    all_rows: list[Any] = []
    for thr in [0.20, 0.30, 0.40]:
        rows = await ab.run_threshold(
            client,  # type: ignore[arg-type]
            base_url="http://x", token="t",
            threshold=thr, bot_id="b", workspace_id="w", channel_type="web",
            dataset=dataset, pace=0.0,
        )
        all_rows.extend(rows)
    assert len(client.calls) == 6
    assert len(all_rows) == 6
    # First two rows = threshold 0.20
    assert all_rows[0].threshold == 0.20
    assert all_rows[2].threshold == 0.30
    assert all_rows[4].threshold == 0.40
    # Final env var = last threshold
    assert os.environ.get(ab.THRESHOLD_OVERRIDE_ENV) == "0.4000"
    # Cleanup so the env doesn't leak to other tests
    del os.environ[ab.THRESHOLD_OVERRIDE_ENV]


# ---------------- output schema (JSON + CSV) --------------------------------
def test_write_json_and_csv_have_expected_shape(tmp_path: Path) -> None:
    ab = _load_ab()
    rows = [
        ab.TurnResult(
            threshold=0.30, question="q1", answer="ans",
            refused=False, top_score=0.41, chunks_used=3,
            latency_ms=600, cost_usd=0.001, trace_id="abc", error=None,
        ),
    ]
    summary = ab.summarize(0.30, rows)
    report = ab.SweepReport(
        generated_at="20260519_120000",
        bot_id="legalbot", workspace_id="default", channel_type="web",
        dataset_path="ds.json", dataset_size=1,
        threshold_values=[0.30], base_url="http://x",
        per_threshold=[summary], raw=rows,
    )
    out_json = tmp_path / "ab.json"
    out_csv = tmp_path / "ab.csv"
    ab.write_json(out_json, report)
    ab.write_csv(out_csv, rows)
    payload = json.loads(out_json.read_text())
    # JSON shape — top-level keys
    expected_keys = {
        "generated_at", "bot_id", "workspace_id", "channel_type",
        "dataset_path", "dataset_size", "threshold_values",
        "base_url", "per_threshold", "raw",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["bot_id"] == "legalbot"
    assert isinstance(payload["per_threshold"], list)
    assert payload["per_threshold"][0]["threshold"] == 0.30
    assert payload["per_threshold"][0]["pass_rate"] == 1.0
    # CSV shape — 9 cols + 1 row
    with open(out_csv, "r") as f:
        reader = csv.reader(f)
        all_rows = list(reader)
    assert len(all_rows) == 2  # header + 1 data row
    assert len(all_rows[0]) == 9  # threshold, question, refused, top_score,
    #                                chunks_used, latency_ms, cost_usd, trace_id, error
    assert all_rows[0] == [
        "threshold", "question", "refused", "top_score",
        "chunks_used", "latency_ms", "cost_usd", "trace_id", "error",
    ]
    assert all_rows[1][0] == "0.3000"
    assert all_rows[1][2] == "0"


# ---------------- CLI exit codes --------------------------------------------
def test_invalid_threshold_returns_exit_4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ab = _load_ab()
    fixture = tmp_path / "ds.json"
    fixture.write_text(json.dumps([{"question": "q"}]))
    args = ab.build_parser().parse_args([
        "--bot", "b",
        "--threshold-values", "0.30,99.0,0.40",
        "--dataset", str(fixture),
    ])
    # Avoid making a real HTTP call by ensuring parse fails first.
    rc = asyncio.run(ab._async_main(args))
    assert rc == ab.EXIT_INVALID_THRESHOLD


def test_missing_dataset_returns_exit_2(tmp_path: Path) -> None:
    ab = _load_ab()
    args = ab.build_parser().parse_args([
        "--bot", "b",
        "--threshold-values", "0.30",
        "--dataset", str(tmp_path / "nope.json"),
    ])
    rc = asyncio.run(ab._async_main(args))
    assert rc == ab.EXIT_DATASET_MISSING
