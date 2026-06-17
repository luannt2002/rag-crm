"""Unit coverage for the batch-10 mode added to `scripts/test_75q_load.py`.

User explicit 2026-04-30: a 75+75 round must be splittable into 10-question
batches with a per-batch checkpoint so an operator can pinpoint which slice
broke (topic, latency outliers, refuse cluster).

Pinned behavior:

1. `--batch-size 0` (default) preserves the prior single-shot output —
   no batch JSON, no batch markdown. (Regression guard.)
2. `--batch-size N > 0` emits one `<output>.batch_<idx>.json` per batch and
   appends to `<output>.batch_log.md` with PASS/REFUSE counts +
   p50/p95 latency.
3. Per-batch summary records the top-K worst REFUSE_NO_DOCS questions
   (preview-truncated) for fast triage.
4. Aggregate summary across all batches matches the single-shot summary
   bucket counts (no double-counting).

Tests are fully offline — `httpx.AsyncClient` is mocked at the
`ask_with_token_refresh` boundary.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module loader — `scripts/` is not a package, so import-by-path. Same shape
# as `tests/unit/test_loadtest_token_refresh.py`.
# ---------------------------------------------------------------------------


def _load_harness() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "test_75q_load.py"
    spec = importlib.util.spec_from_file_location("_t75q_harness_batch", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load_harness()


def _make_turn(harness: ModuleType, **overrides: Any) -> Any:
    """Build a TurnResult with sensible defaults for tests."""
    base: dict[str, Any] = {
        "room": 1,
        "idx": 0,
        "question": "what is the price?",
        "classification": "PASS",
        "answer": "the documented price is X.",
        "chunks_used": 3,
        "top_score": 0.71,
        "duration_ms": 1200,
        "wall_ms": 1300.0,
        "cost_usd": 0.001,
    }
    base.update(overrides)
    return harness.TurnResult(**base)


# ---------------------------------------------------------------------------
# Test 1 — Pure logic: slice_into_batches respects batch size + edge cases.
# ---------------------------------------------------------------------------


def test_slice_into_batches_handles_uneven_tail(harness: ModuleType) -> None:
    turns = [_make_turn(harness, idx=i) for i in range(25)]
    batches = harness.slice_into_batches(turns, batch_size=10)
    # 25 turns / batch=10 → [10, 10, 5]
    assert [len(b[2]) for b in batches] == [10, 10, 5]
    # Ranges 1-indexed inclusive.
    assert [b[0] for b in batches] == [1, 11, 21]
    assert [b[1] for b in batches] == [10, 20, 25]


def test_slice_into_batches_zero_returns_empty(harness: ModuleType) -> None:
    turns = [_make_turn(harness, idx=i) for i in range(5)]
    assert harness.slice_into_batches(turns, batch_size=0) == []
    assert harness.slice_into_batches(turns, batch_size=-1) == []


# ---------------------------------------------------------------------------
# Test 2 — summarize_batch counts buckets + selects top-N worst REFUSE_NO_DOCS.
# ---------------------------------------------------------------------------


def test_per_batch_summary_includes_top_3_worst_refuse(harness: ModuleType) -> None:
    long_q = "Câu hỏi kiểm thử về thông tin tài liệu hệ thống (domain-neutral)"
    turns = [
        _make_turn(harness, idx=0, classification="PASS", duration_ms=900),
        _make_turn(
            harness,
            idx=1,
            classification="REFUSE_NO_DOCS",
            answer="xin lỗi tôi không có thông tin",
            chunks_used=0,
            top_score=0.0,
            question=long_q + " #1 extra padding to exceed preview length easily",
            duration_ms=2200,
        ),
        _make_turn(
            harness,
            idx=2,
            classification="REFUSE_NO_DOCS",
            answer="không tìm thấy",
            chunks_used=0,
            question=long_q + " #2",
            duration_ms=1100,
        ),
        _make_turn(
            harness,
            idx=3,
            classification="REFUSE_NO_DOCS",
            answer="chưa có",
            chunks_used=0,
            question=long_q + " #3",
            duration_ms=950,
        ),
        _make_turn(
            harness,
            idx=4,
            classification="REFUSE_NO_DOCS",
            answer="chưa có",
            chunks_used=0,
            question=long_q + " #4 — should NOT appear (top-3 only)",
        ),
        _make_turn(harness, idx=5, classification="FAIL", answer="", duration_ms=300),
    ]
    s = harness.summarize_batch(turns, top_n_worst_refuse=3, preview_chars=80)
    assert s["counts"]["PASS"] == 1
    assert s["counts"]["REFUSE_NO_DOCS"] == 4
    assert s["counts"]["FAIL"] == 1
    worst = s["worst_refuse_no_docs"]
    assert len(worst) == 3, "must cap at top_n_worst_refuse"
    # Preview char truncation honored.
    for w in worst:
        assert len(w["question_preview"]) <= 80
    # First one is the earliest by (room, idx) — deterministic ordering.
    assert worst[0]["idx"] == 1
    # p50/p95 sane on 5 positive durations.
    assert s["latency_ms_p95"] >= s["latency_ms_p50"] >= 0


# ---------------------------------------------------------------------------
# Test 3 — Markdown formatter renders header + bucket table + worst list.
# ---------------------------------------------------------------------------


def test_format_batch_markdown_emits_header_and_table(harness: ModuleType) -> None:
    turns = [
        _make_turn(harness, idx=0, classification="PASS"),
        _make_turn(
            harness,
            idx=1,
            classification="REFUSE_NO_DOCS",
            chunks_used=0,
            answer="xin lỗi",
            question="Bot có biết giá không?",
        ),
    ]
    s = harness.summarize_batch(turns)
    md = harness.format_batch_markdown(
        batch_idx=2, total_batches=8, turn_range=(11, 20), summary=s
    )
    assert "## Batch 2/8 — turns 11-20" in md
    assert "| Bucket | Count |" in md
    assert "| PASS | 1 |" in md
    assert "| REFUSE_NO_DOCS | 1 |" in md
    assert "Top worst REFUSE_NO_DOCS:" in md
    assert "Bot có biết giá không?" in md


# ---------------------------------------------------------------------------
# Test 4 — End-to-end: --batch-size 10 emits intermediate JSON + markdown log.
# ---------------------------------------------------------------------------


def _scripted_responses(n: int) -> list[tuple[int, dict[str, Any], float]]:
    """Generate `n` scripted (status, body, wall_ms) triples.

    Mix of PASS and REFUSE_NO_DOCS so the summary has interesting buckets.
    """
    out: list[tuple[int, dict[str, Any], float]] = []
    for i in range(n):
        if i % 3 == 0:
            # REFUSE_NO_DOCS — chunks_used=0 + refuse phrase.
            body: dict[str, Any] = {
                "answer": "Xin lỗi, tôi không có thông tin về câu hỏi này.",
                "chunks_used": 0,
                "top_score": 0.0,
                "duration_ms": 800 + i,
                "cost_usd": 0.0001,
                "tokens": {"prompt": 100, "completion": 30, "cached": 0},
            }
        else:
            body = {
                "answer": "Theo tài liệu, câu trả lời chi tiết là " + ("x" * 60),
                "chunks_used": 4,
                "top_score": 0.65,
                "duration_ms": 1500 + i,
                "cost_usd": 0.0009,
                "tokens": {"prompt": 800, "completion": 120, "cached": 200},
            }
        out.append((200, body, float(body["duration_ms"])))
    return out


@pytest.mark.asyncio
async def test_batch_size_10_emits_intermediate_files(
    harness: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prepare a tiny questions file with 2 rooms × 12 questions = 24 turns.
    md = ["# Mock", ""]
    for room in (1, 2):
        md.append(f"## Room {room}")
        md.append("")
        for i in range(12):
            md.append(f"{i + 1}. Câu hỏi {room}-{i + 1}?")
        md.append("")
    qfile = tmp_path / "questions.md"
    qfile.write_text("\n".join(md), encoding="utf-8")

    out_path = tmp_path / "out.json"

    # Stub the network boundary.
    scripted = _scripted_responses(24)
    call_idx = {"i": 0}

    async def fake_ask(*a: Any, **kw: Any) -> tuple[int, dict[str, Any], float]:
        i = call_idx["i"]
        call_idx["i"] += 1
        return scripted[i]

    async def fake_token(*a: Any, **kw: Any) -> str:
        return "FAKE_TOKEN"

    monkeypatch.setattr(harness, "ask_with_token_refresh", fake_ask)
    monkeypatch.setattr(harness, "get_self_token", fake_token)

    args = harness.argparse.Namespace(
        bot_id="test-bot",
        tenant_id=1,
        channel_type="web",
        base_url="http://localhost:0",
        rooms="1,2",
        questions_file=str(qfile),
        bypass_cache=True,
        debug="",
        inter_room_sleep=0.0,
        inter_question_sleep=0.0,
        batch_size=10,
        output=str(out_path),
    )

    rc = await harness.main_async(args)
    assert rc == 0

    # Aggregate JSON exists + has 24 turns.
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(raw["turns"]) == 24
    assert raw["config"]["batch_size"] == 10

    # 3 batch JSONs emitted: 10 + 10 + 4.
    b1 = tmp_path / "out.batch_01.json"
    b2 = tmp_path / "out.batch_02.json"
    b3 = tmp_path / "out.batch_03.json"
    assert b1.exists() and b2.exists() and b3.exists()
    assert len(json.loads(b1.read_text())["turns"]) == 10
    assert len(json.loads(b2.read_text())["turns"]) == 10
    assert len(json.loads(b3.read_text())["turns"]) == 4

    # Batch log markdown exists with the 3 headers.
    log = (tmp_path / "out.batch_log.md").read_text(encoding="utf-8")
    assert "Batch 1/3" in log
    assert "Batch 2/3" in log
    assert "Batch 3/3" in log
    assert "| Bucket | Count |" in log

    # Aggregate bucket counts == sum of per-batch bucket counts.
    agg = raw["summary"]["counts"]
    sum_counts: dict[str, int] = {}
    for bf in (b1, b2, b3):
        for k, v in json.loads(bf.read_text())["summary"]["counts"].items():
            sum_counts[k] = sum_counts.get(k, 0) + int(v)
    assert agg == sum_counts


# ---------------------------------------------------------------------------
# Test 5 — Default behavior preserved: --batch-size 0 → no batch artefacts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_size_zero_preserves_single_shot_behavior(
    harness: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = [
        "# Mock",
        "",
        "## Room 1",
        "",
    ]
    for i in range(5):
        md.append(f"{i + 1}. Câu hỏi {i + 1}?")
    qfile = tmp_path / "questions.md"
    qfile.write_text("\n".join(md), encoding="utf-8")
    out_path = tmp_path / "agg.json"

    scripted = _scripted_responses(5)
    call_idx = {"i": 0}

    async def fake_ask(*a: Any, **kw: Any) -> tuple[int, dict[str, Any], float]:
        i = call_idx["i"]
        call_idx["i"] += 1
        return scripted[i]

    async def fake_token(*a: Any, **kw: Any) -> str:
        return "T"

    monkeypatch.setattr(harness, "ask_with_token_refresh", fake_ask)
    monkeypatch.setattr(harness, "get_self_token", fake_token)

    args = harness.argparse.Namespace(
        bot_id="b",
        tenant_id=1,
        channel_type="web",
        base_url="http://x",
        rooms="1",
        questions_file=str(qfile),
        bypass_cache=True,
        debug="",
        inter_room_sleep=0.0,
        inter_question_sleep=0.0,
        batch_size=0,  # default — no batch mode
        output=str(out_path),
    )

    rc = await harness.main_async(args)
    assert rc == 0
    assert out_path.exists()
    # NO batch artefacts created.
    assert not (tmp_path / "agg.batch_01.json").exists()
    assert not (tmp_path / "agg.batch_log.md").exists()


# ---------------------------------------------------------------------------
# Test 6 — Post-hoc analyser CLI re-emits batch markdown from a saved JSON.
# ---------------------------------------------------------------------------


def test_post_hoc_analyzer_rebuilds_batch_markdown(
    harness: ModuleType, tmp_path: Path
) -> None:
    # Synthesise an aggregate JSON shape that the analyser consumes.
    turns_json: list[dict[str, Any]] = []
    for i in range(15):
        cls = "PASS" if i % 2 == 0 else "REFUSE_NO_DOCS"
        turns_json.append(
            {
                "room": 1 + (i // 8),
                "idx": i % 8,
                "question": f"Q{i}",
                "classification": cls,
                "answer": "ok" if cls == "PASS" else "xin lỗi tôi không có thông tin",
                "chunks_used": 3 if cls == "PASS" else 0,
                "top_score": 0.6 if cls == "PASS" else 0.0,
                "duration_ms": 1000 + i,
                "wall_ms": 1100.0 + i,
                "cost_usd": 0.0005,
            }
        )
    agg_path = tmp_path / "round_x.json"
    agg_path.write_text(
        json.dumps(
            {
                "config": {
                    "bot_id": "b",
                    "tenant_id": 1,
                    "channel_type": "web",
                    "rooms": [1, 2],
                },
                "summary": {"total_turns": 15},
                "turns": turns_json,
            }
        ),
        encoding="utf-8",
    )

    # Import analyser by file path (same trick as harness).
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_lt_batch_analyze_test", repo_root / "scripts" / "loadtest_batch_analyze.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    md, summaries = mod.analyze(agg_path, batch_size=10)
    assert "## Batch 1/2 — turns 1-10" in md
    assert "## Batch 2/2 — turns 11-15" in md
    assert len(summaries) == 2
    # Bucket counts in batch 1 include both classes.
    s1 = summaries[0]["summary"]["counts"]
    assert s1.get("PASS", 0) + s1.get("REFUSE_NO_DOCS", 0) == 10
