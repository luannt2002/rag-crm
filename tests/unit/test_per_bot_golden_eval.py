"""Unit tests for ``scripts/eval_per_bot_golden.py``.

Mock-only: no Postgres / Redis / HTTP. The CLI accepts an injectable
``runner`` so unit tests drive deterministic responses and assert on
exit codes and parsed args.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

# scripts/ is not a package — load via sys.path injection like
# tests/unit/test_eval_tools.py.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import eval_per_bot_golden  # type: ignore  # noqa: E402


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(obj) for obj in lines) + "\n",
        encoding="utf-8",
    )


def test_arg_parser_accepts_golden_dir_baseline_tolerance(tmp_path: Path) -> None:
    """CLI parses --golden-dir, --baseline, --tolerance correctly."""
    parser = eval_per_bot_golden.build_arg_parser()
    args = parser.parse_args(
        [
            "--golden-dir",
            str(tmp_path / "g"),
            "--baseline",
            str(tmp_path / "b.jsonl"),
            "--tolerance",
            "0.05",
        ]
    )
    assert args.golden_dir == tmp_path / "g"
    assert args.baseline == tmp_path / "b.jsonl"
    assert args.tolerance == pytest.approx(0.05)

    # Defaults: golden-dir defaults to Path("golden_set"), baseline None,
    # tolerance equals the module-level strict default.
    defaults = parser.parse_args([])
    assert defaults.golden_dir == Path("golden_set")
    assert defaults.baseline is None
    assert defaults.tolerance == eval_per_bot_golden.DEFAULT_REGRESSION_TOLERANCE


def test_empty_golden_dir_returns_zero(tmp_path: Path) -> None:
    """Missing or empty golden dir → exit 0 (no-op), runner never invoked."""
    empty_dir = tmp_path / "no-bots-here"
    empty_dir.mkdir()
    invocations: list[tuple[str, str]] = []

    def runner(record_bot_id: str, question: str) -> Mapping[str, object]:
        invocations.append((record_bot_id, question))
        return {"answer": "should not be called"}

    rc = eval_per_bot_golden.main(
        ["--golden-dir", str(empty_dir)],
        runner=runner,
    )
    assert rc == 0
    assert invocations == []


def test_regression_below_baseline_returns_one(tmp_path: Path) -> None:
    """Bot pass rate below baseline → exit 1 with regression detected."""
    golden_dir = tmp_path / "golden_set"
    golden_dir.mkdir()
    bot_id = "bot-uuid-alpha"
    # 4 entries, runner answers correctly only on the first → pass_rate = 0.25.
    _write_jsonl(
        golden_dir / f"{bot_id}.jsonl",
        [
            {"question": "q1", "expected_answer": "yes", "must_cite": False},
            {"question": "q2", "expected_answer": "yes", "must_cite": False},
            {"question": "q3", "expected_answer": "yes", "must_cite": False},
            {"question": "q4", "expected_answer": "yes", "must_cite": False},
        ],
    )
    baseline_path = tmp_path / "baseline.jsonl"
    _write_jsonl(
        baseline_path,
        [{"record_bot_id": bot_id, "baseline_pass_rate": 0.80}],
    )

    def runner(record_bot_id: str, question: str) -> Mapping[str, object]:  # noqa: ARG001 — runner signature contract
        if question == "q1":
            return {"answer": "yes, that is correct"}
        return {"answer": "no, that is wrong"}

    rc = eval_per_bot_golden.main(
        [
            "--golden-dir",
            str(golden_dir),
            "--baseline",
            str(baseline_path),
        ],
        runner=runner,
    )
    assert rc == 1

    # Cross-check: the regression detector reports the right delta.
    entries = eval_per_bot_golden.parse_golden_entries(
        golden_dir / f"{bot_id}.jsonl"
    )
    result = eval_per_bot_golden.run_bot(bot_id, entries, runner)
    assert result.total == 4
    assert result.passed == 1
    assert result.pass_rate == pytest.approx(0.25)
    regressions = eval_per_bot_golden.detect_regressions(
        [result],
        {bot_id: 0.80},
        tolerance=0.0,
    )
    assert regressions == [(bot_id, pytest.approx(0.25), 0.80)]


def test_all_bots_pass_returns_zero(tmp_path: Path) -> None:
    """Every bot meets baseline → exit 0; multi-bot results all pass."""
    golden_dir = tmp_path / "golden_set"
    golden_dir.mkdir()
    bot_a = "bot-uuid-a"
    bot_b = "bot-uuid-b"
    _write_jsonl(
        golden_dir / f"{bot_a}.jsonl",
        [
            {
                "question": "q1",
                "expected_answer": "ok",
                "expected_intent": "factoid",
                "must_cite": True,
            },
            {
                "question": "q2",
                "expected_answer": "ok",
                "expected_intent": "factoid",
                "must_cite": True,
            },
        ],
    )
    _write_jsonl(
        golden_dir / f"{bot_b}.jsonl",
        [
            {
                "question": "hi",
                "expected_intent": "greeting",
                "must_cite": False,
            },
        ],
    )
    baseline_path = tmp_path / "baseline.jsonl"
    _write_jsonl(
        baseline_path,
        [
            {"record_bot_id": bot_a, "baseline_pass_rate": 0.90},
            {"record_bot_id": bot_b, "baseline_pass_rate": 0.50},
        ],
    )

    def runner(record_bot_id: str, question: str) -> Mapping[str, object]:  # noqa: ARG001 — runner signature contract
        if record_bot_id == bot_a:
            return {
                "answer": "ok, here you go",
                "intent": "factoid",
                "citations": ["doc-1"],
            }
        return {"answer": "hello there", "intent": "greeting"}

    rc = eval_per_bot_golden.main(
        [
            "--golden-dir",
            str(golden_dir),
            "--baseline",
            str(baseline_path),
        ],
        runner=runner,
    )
    assert rc == 0

    # Direct-call cross-check: both bots score 1.0.
    entries_a = eval_per_bot_golden.parse_golden_entries(
        golden_dir / f"{bot_a}.jsonl"
    )
    entries_b = eval_per_bot_golden.parse_golden_entries(
        golden_dir / f"{bot_b}.jsonl"
    )
    result_a = eval_per_bot_golden.run_bot(bot_a, entries_a, runner)
    result_b = eval_per_bot_golden.run_bot(bot_b, entries_b, runner)
    assert result_a.passed == 2 and result_a.total == 2
    assert result_b.passed == 1 and result_b.total == 1
    assert result_a.pass_rate == pytest.approx(1.0)
    assert result_b.pass_rate == pytest.approx(1.0)
    # Must_cite enforcement: drop citations → bot_a fails its rubric.
    no_cite_response = {"answer": "ok", "intent": "factoid", "citations": []}
    assert eval_per_bot_golden.evaluate_entry(entries_a[0], no_cite_response) is False
