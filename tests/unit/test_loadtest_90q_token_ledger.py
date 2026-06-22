"""Ledger schema + token-mapping gate for scripts/loadtest_90q_multi_bot.py.

M31 fix: the per-question ledger captured latency/cost/chunks/score but NOT
tokens_in / tokens_out. The chat response body exposes usage under
``tokens.{prompt,completion}`` (verified live: prompt=3369, completion=140).
These tests lock:
  1. ``extract_tokens`` maps a real response dict → (tokens_in, tokens_out).
  2. an absent ``tokens`` field (error path) records (None, None), never a
     fabricated zero — HALLU=0 applies to eval ledgers too.
  3. ``build_record`` emits ``tokens_in`` / ``tokens_out`` columns.
  4. ``summarize`` aggregates token totals/averages over real rows only.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
from scripts.loadtest_90q_multi_bot import (  # noqa: E402
    build_record,
    extract_tokens,
    summarize,
)


def test_extract_tokens_from_real_response() -> None:
    """Maps tokens.prompt → tokens_in, tokens.completion → tokens_out."""
    resp = {
        "answer": "Dạ gói 10 buổi giá 60.000 đồng ạ.",
        "tokens": {"prompt": 3369, "completion": 140, "cached": 0},
        "cost_usd": 0.0015716,
    }
    assert extract_tokens(resp) == (3369, 140)


def test_extract_tokens_absent_field_is_none_not_zero() -> None:
    """Error path returns no ``tokens`` key → (None, None), never (0, 0).

    Recording 0 would fabricate a measurement that did not happen; HALLU=0
    forbids inventing a value for an absent field.
    """
    err_resp = {"error": "ReadTimeout", "latency_ms": 120000}
    assert extract_tokens(err_resp) == (None, None)


def test_extract_tokens_quota_blocked_zero_is_real() -> None:
    """Quota-blocked path emits a real {prompt:0, completion:0} → (0, 0)."""
    blocked = {"ok": False, "blocked": True, "tokens": {"prompt": 0, "completion": 0}}
    assert extract_tokens(blocked) == (0, 0)


def test_build_record_includes_token_columns() -> None:
    """Ledger row schema now carries tokens_in / tokens_out."""
    turn = {
        "id": "q1",
        "bot_id": "test-spa-id",
        "question": "Cho tôi hỏi giá",
    }
    resp = {
        "answer": "Dạ 60.000 đồng ạ.",
        "tokens": {"prompt": 100, "completion": 20, "cached": 0},
        "cost_usd": 0.0001,
        "chunks_used": 2,
        "top_score": 0.36,
        "latency_ms": 800,
    }
    rec = build_record(turn, resp, verdict="PASS_ANSWERED")
    assert "tokens_in" in rec
    assert "tokens_out" in rec
    assert rec["tokens_in"] == 100
    assert rec["tokens_out"] == 20


def test_build_record_error_row_records_null_tokens() -> None:
    turn = {"id": "q2", "bot_id": "b", "question": "x"}
    resp = {"error": "ReadTimeout", "latency_ms": 120000}
    rec = build_record(turn, resp, verdict="ERR")
    assert rec["tokens_in"] is None
    assert rec["tokens_out"] is None


def test_summarize_aggregates_real_tokens_only() -> None:
    """Token totals/averages skip null rows (absent measurements)."""
    rows = [
        {"verdict": "PASS_ANSWERED", "hallu_trap": False, "latency_ms": 500,
         "cost_usd": 0.001, "tokens_in": 100, "tokens_out": 20},
        {"verdict": "PASS_ANSWERED", "hallu_trap": False, "latency_ms": 700,
         "cost_usd": 0.002, "tokens_in": 300, "tokens_out": 40},
        # error row — null tokens must NOT be counted as 0
        {"verdict": "ERR", "hallu_trap": False, "latency_ms": 120000,
         "cost_usd": None, "tokens_in": None, "tokens_out": None},
    ]
    s = summarize(rows, label="t")
    assert s["total_tokens_in"] == 400
    assert s["total_tokens_out"] == 60
    assert s["avg_tokens_in"] == 200.0
    assert s["avg_tokens_out"] == 30.0
