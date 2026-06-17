"""Smoke tests for the vertical-agnostic golden test harness.

These tests exercise the ``GoldenTestRunner`` against an in-process mock
HTTP transport so we never need a live ragbot deployment to verify:

- Fixture loading (YAML parse + structure).
- Per-question scoring (keyword match, banned-term hallucination guard).
- Aggregate metrics (PASS rate, mean faith, mean top_score, p95 latency).
- Floor enforcement (``EvalFloorViolation`` raises on shortfall).
- 3-key identity is forwarded correctly to the chat endpoint.
- Mixed-language fixtures (VN + EN) pass through the same code path with
  zero language-specific branches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.eval.constants import DEFAULT_GOLDEN_CHAT_PATH
from tests.eval.golden_runner import (
    EvalFloorViolation,
    EvalResult,
    GoldenTestRunner,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

EXPECTED_TENANT_ID = 4242
EXPECTED_BOT_ID = "<bot-slug>"
EXPECTED_CHANNEL_TYPE = "<channel>"
EXPECTED_BASE_URL = "http://ragbot.test"


def _bot_3key() -> tuple[int, str, str]:
    return (EXPECTED_TENANT_ID, EXPECTED_BOT_ID, EXPECTED_CHANNEL_TYPE)


def _make_handler(
    answer_for_intent: dict[str, str],
    *,
    captured: list[dict[str, Any]] | None = None,
    faith: float = 0.92,
    top_score: float = 0.71,
    latency_ms: float = 1234.0,
) -> httpx.MockTransport:
    """Build an httpx MockTransport that returns canned answers per intent."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if captured is not None:
            captured.append(body)
        message = body.get("message", "")
        # naive: pick the answer whose key is contained in the message; else fallback
        chosen = ""
        for needle, ans in answer_for_intent.items():
            if needle and needle in message:
                chosen = ans
                break
        if not chosen:
            chosen = answer_for_intent.get("__default__", "ok")
        payload = {
            "answer": chosen,
            "faithfulness": faith,
            "top_score": top_score,
            "latency_ms": latency_ms,
        }
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


# ----------------------------------------------------------------------
# Fixture loading
# ----------------------------------------------------------------------


def test_runner_loads_spa_fixture() -> None:
    runner = GoldenTestRunner(
        vertical="spa",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    fixture = runner.load_fixture()
    assert fixture["vertical"] == "spa"
    assert isinstance(fixture["questions"], list)
    assert len(fixture["questions"]) >= 15


def test_runner_loads_finance_fixture() -> None:
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    fixture = runner.load_fixture()
    assert fixture["vertical"] == "finance"
    assert fixture["language"] == "en"
    assert len(fixture["questions"]) >= 15


def test_runner_rejects_missing_fixture() -> None:
    runner = GoldenTestRunner(
        vertical="does-not-exist",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    with pytest.raises(FileNotFoundError):
        runner.load_fixture()


def test_floor_block_loads_from_fixture() -> None:
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    floor = runner._effective_floor()
    assert floor["pass_rate"] > 0.0
    assert floor["faithfulness"] > 0.0
    assert floor["p95_ms"] > 0.0


# ----------------------------------------------------------------------
# 3-key identity validation
# ----------------------------------------------------------------------


def test_runner_rejects_missing_tenant_id_type() -> None:
    with pytest.raises(ValueError):
        GoldenTestRunner(
            vertical="spa",
            fixtures_dir=FIXTURES_DIR,
            bot_3key=("not-an-int", EXPECTED_BOT_ID, EXPECTED_CHANNEL_TYPE),  # type: ignore[arg-type]
            base_url=EXPECTED_BASE_URL,
        )


def test_runner_rejects_empty_bot_id() -> None:
    with pytest.raises(ValueError):
        GoldenTestRunner(
            vertical="spa",
            fixtures_dir=FIXTURES_DIR,
            bot_3key=(EXPECTED_TENANT_ID, "", EXPECTED_CHANNEL_TYPE),
            base_url=EXPECTED_BASE_URL,
        )


def test_runner_rejects_empty_channel() -> None:
    with pytest.raises(ValueError):
        GoldenTestRunner(
            vertical="spa",
            fixtures_dir=FIXTURES_DIR,
            bot_3key=(EXPECTED_TENANT_ID, EXPECTED_BOT_ID, ""),
            base_url=EXPECTED_BASE_URL,
        )


def test_post_payload_carries_3key_correctly() -> None:
    captured: list[dict[str, Any]] = []
    transport = _make_handler({"__default__": "welcome and help to start"}, captured=captured)
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
        transport=transport,
    )
    runner.run()
    assert len(captured) >= 15
    first = captured[0]
    assert first["tenant_id"] == EXPECTED_TENANT_ID
    assert first["bot_id"] == EXPECTED_BOT_ID
    assert first["channel_type"] == EXPECTED_CHANNEL_TYPE
    assert "message" in first and isinstance(first["message"], str)


# ----------------------------------------------------------------------
# Metric aggregation
# ----------------------------------------------------------------------


def test_run_computes_perfect_pass_when_all_keywords_match() -> None:
    one_size_fits_all = (
        "Welcome to help start; limit withdrawal daily; replacement lost card; "
        "document open account; fee wire transfer; statement download month; "
        "unauthorized report transaction; branch hours weekend; enroll mobile banking; "
        "rate deposit term; increase limit credit; sorry cannot unable; "
        "which what more; monthly fee checking; identification wire transfer."
    )
    transport = _make_handler(
        {"__default__": one_size_fits_all},
        faith=0.95,
        top_score=0.8,
        latency_ms=1500.0,
    )
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
        transport=transport,
    )
    result: EvalResult = runner.run()
    assert result.total >= 15
    assert result.passed == result.total
    assert result.pass_rate == pytest.approx(1.0)
    assert result.mean_faithfulness == pytest.approx(0.95)
    assert result.mean_top_score == pytest.approx(0.8)
    assert result.hallu_count == 0


def test_run_detects_hallucination_via_banned_term() -> None:
    bad_answer = "Sure, our CEO phone: +1-555-0100 is available 24/7"
    transport = _make_handler(
        {"CEO": bad_answer, "__default__": "ok"},
        faith=0.5,
        top_score=0.4,
        latency_ms=2000.0,
    )
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
        transport=transport,
    )
    result = runner.run()
    assert result.hallu_count >= 1


# ----------------------------------------------------------------------
# Floor enforcement
# ----------------------------------------------------------------------


def test_assert_meets_floor_raises_when_below() -> None:
    transport = _make_handler(
        {"__default__": "irrelevant"},
        faith=0.1,
        top_score=0.1,
        latency_ms=99999.0,
    )
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
        transport=transport,
    )
    with pytest.raises(EvalFloorViolation) as exc:
        runner.assert_meets_floor()
    msg = str(exc.value)
    assert "pass_rate" in msg or "faithfulness" in msg or "top_score" in msg or "p95_ms" in msg


def test_assert_meets_floor_passes_when_all_meet() -> None:
    one_size_fits_all = (
        "Welcome to help start; limit withdrawal daily; replacement lost card; "
        "document open account; fee wire transfer; statement download month; "
        "unauthorized report transaction; branch hours weekend; enroll mobile banking; "
        "rate deposit term; increase limit credit; sorry cannot unable; "
        "which what more; monthly fee checking; identification wire transfer."
    )
    transport = _make_handler(
        {"__default__": one_size_fits_all},
        faith=0.95,
        top_score=0.8,
        latency_ms=1500.0,
    )
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
        transport=transport,
    )
    outcome = runner.assert_meets_floor()
    assert outcome.shortfalls() == []


# ----------------------------------------------------------------------
# Mixed-language verticals share the same code path
# ----------------------------------------------------------------------


def test_vn_and_en_fixtures_use_same_runner_class() -> None:
    spa_runner = GoldenTestRunner(
        vertical="spa",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    finance_runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    assert type(spa_runner) is type(finance_runner)
    spa_runner.load_fixture()
    finance_runner.load_fixture()


def test_chat_path_default_matches_constant() -> None:
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
    )
    assert runner._chat_path == DEFAULT_GOLDEN_CHAT_PATH


# ----------------------------------------------------------------------
# HTTP error robustness
# ----------------------------------------------------------------------


def test_http_500_marks_question_failed_without_raising() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    transport = httpx.MockTransport(handler)
    runner = GoldenTestRunner(
        vertical="finance",
        fixtures_dir=FIXTURES_DIR,
        bot_3key=_bot_3key(),
        base_url=EXPECTED_BASE_URL,
        transport=transport,
    )
    result = runner.run()
    assert result.passed == 0
    assert result.pass_rate == pytest.approx(0.0)
