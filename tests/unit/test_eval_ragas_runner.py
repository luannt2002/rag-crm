"""Unit tests for ``scripts/eval_ragas.py``.

Mocks the RAGAS metric layer (the ``MetricScorer`` Port) so these tests
never reach OpenAI. Predictions are injected too — no live HTTP. The
tests cover CLI parsing, dataset I/O, gate logic, report shape, and
error handling.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Load scripts/eval_ragas.py as a module (it is not on the import path
# the way `src/ragbot/*` is). We use importlib to keep the script itself
# CLI-shaped without forcing a sys.path hack.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "eval_ragas.py"
_spec = importlib.util.spec_from_file_location("eval_ragas_under_test", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
eval_ragas = importlib.util.module_from_spec(_spec)
sys.modules["eval_ragas_under_test"] = eval_ragas
_spec.loader.exec_module(eval_ragas)


class _StubScorer:
    """Deterministic scorer used in every test below."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.calls: list[list[dict[str, Any]]] = []

    def score(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        self.calls.append(rows)
        return dict(self._scores)


def _write_dataset(tmp_path: Path, items: list[dict[str, Any]]) -> Path:
    out = tmp_path / "ds.json"
    out.write_text(json.dumps(items, ensure_ascii=False))
    return out


def _make_predictions(n: int, *, answered: int | None = None) -> list[dict[str, Any]]:
    """Build N synthetic prediction rows. ``answered`` controls how many
    of them carry a non-empty ``answer`` (rest are simulated errors)."""
    answered = n if answered is None else answered
    rows: list[dict[str, Any]] = []
    for idx in range(n):
        has_answer = idx < answered
        rows.append(
            {
                "question": f"q{idx}",
                "answer": f"a{idx}" if has_answer else "",
                "contexts": [f"ctx{idx}"],
                "ground_truth": f"gt{idx}",
                "latency_ms": 123,
                "error": None if has_answer else "transport",
            }
        )
    return rows


# ───────────────────────────── CLI parsing ──────────────────────────────


def test_build_parser_requires_bot_and_dataset() -> None:
    parser = eval_ragas.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_default_thresholds_match_constants() -> None:
    from ragbot.shared.constants import (
        DEFAULT_RAGAS_FAITHFULNESS_GATE,
        DEFAULT_RAGAS_RELEVANCY_GATE,
    )

    parser = eval_ragas.build_parser()
    args = parser.parse_args(["--bot", "x", "--dataset", "/tmp/x.json"])
    assert args.faithfulness_gate == pytest.approx(DEFAULT_RAGAS_FAITHFULNESS_GATE)
    assert args.relevancy_gate == pytest.approx(DEFAULT_RAGAS_RELEVANCY_GATE)


def test_build_parser_channel_type_default_is_web() -> None:
    parser = eval_ragas.build_parser()
    args = parser.parse_args(["--bot", "x", "--dataset", "/tmp/x.json"])
    assert args.channel_type == "web"


# ───────────────────────────── Dataset I/O ──────────────────────────────


def test_load_dataset_happy_path(tmp_path: Path) -> None:
    items = [
        {
            "question": "Q1",
            "ground_truth_answer": "A1",
            "ground_truth_contexts": ["c1"],
        }
    ]
    path = _write_dataset(tmp_path, items)
    loaded = eval_ragas.load_dataset(path)
    assert loaded == items


def test_load_dataset_rejects_non_list(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"not": "list"}')
    with pytest.raises(ValueError, match="top-level JSON must be a list"):
        eval_ragas.load_dataset(path)


def test_load_dataset_rejects_missing_field(tmp_path: Path) -> None:
    items = [{"question": "Q", "ground_truth_answer": "A"}]  # no contexts
    path = _write_dataset(tmp_path, items)
    with pytest.raises(ValueError, match="ground_truth_contexts"):
        eval_ragas.load_dataset(path)


def test_load_dataset_rejects_empty_question(tmp_path: Path) -> None:
    items = [
        {"question": "  ", "ground_truth_answer": "A", "ground_truth_contexts": []}
    ]
    path = _write_dataset(tmp_path, items)
    with pytest.raises(ValueError, match="question must be non-empty"):
        eval_ragas.load_dataset(path)


def test_load_dataset_rejects_non_string_context(tmp_path: Path) -> None:
    items = [
        {
            "question": "Q",
            "ground_truth_answer": "A",
            "ground_truth_contexts": [123],
        }
    ]
    path = _write_dataset(tmp_path, items)
    with pytest.raises(ValueError, match="list\\[str\\]"):
        eval_ragas.load_dataset(path)


# ───────────────────────────── Gate logic ───────────────────────────────


def test_evaluate_gates_pass() -> None:
    metrics = {
        "faithfulness": 0.95,
        "answer_relevancy": 0.90,
        "context_precision": 0.5,
        "context_recall": 0.5,
    }
    assert eval_ragas.evaluate_gates(
        metrics, faithfulness_gate=0.85, relevancy_gate=0.80
    )


def test_evaluate_gates_fail_on_faithfulness() -> None:
    metrics = {
        "faithfulness": 0.70,
        "answer_relevancy": 0.95,
        "context_precision": 1.0,
        "context_recall": 1.0,
    }
    assert not eval_ragas.evaluate_gates(
        metrics, faithfulness_gate=0.85, relevancy_gate=0.80
    )


def test_evaluate_gates_fail_on_relevancy() -> None:
    metrics = {
        "faithfulness": 0.95,
        "answer_relevancy": 0.50,
        "context_precision": 1.0,
        "context_recall": 1.0,
    }
    assert not eval_ragas.evaluate_gates(
        metrics, faithfulness_gate=0.85, relevancy_gate=0.80
    )


# ───────────────────────────── Output report ────────────────────────────


def test_default_output_path_includes_bot_slug() -> None:
    p = eval_ragas.default_output_path("bot-legal-pilot")
    assert "bot-legal-pilot" in p.name
    assert p.suffix == ".json"
    assert p.parent.name == "reports"


def test_write_report_shape(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    rows = _make_predictions(2)
    metrics = {
        "faithfulness": 0.9,
        "answer_relevancy": 0.85,
        "context_precision": 0.7,
        "context_recall": 0.75,
    }
    eval_ragas.write_report(
        output_path=out,
        bot_id="bot-x",
        dataset_path=tmp_path / "ds.json",
        rows=rows,
        metrics=metrics,
        gates={"faithfulness": 0.85, "answer_relevancy": 0.80},
        pass_gates=True,
    )
    data = json.loads(out.read_text())
    assert data["bot_id"] == "bot-x"
    assert data["n_rows"] == 2
    assert data["metrics"] == metrics
    assert data["pass_gates"] is True
    assert isinstance(data["rows"], list) and len(data["rows"]) == 2
    assert "generated_at" in data


# ───────────────────────────── End-to-end run() ─────────────────────────


def _run_with_scorer(
    tmp_path: Path,
    scores: dict[str, float],
    *,
    n_items: int = 2,
    extra_args: list[str] | None = None,
) -> tuple[int, Path]:
    items = [
        {
            "question": f"Q{idx}",
            "ground_truth_answer": f"A{idx}",
            "ground_truth_contexts": [f"c{idx}"],
        }
        for idx in range(n_items)
    ]
    ds = _write_dataset(tmp_path, items)
    out = tmp_path / "report.json"
    parser = eval_ragas.build_parser()
    argv = [
        "--bot",
        "bot-legal-pilot",
        "--dataset",
        str(ds),
        "--output",
        str(out),
    ]
    if extra_args:
        argv.extend(extra_args)
    args = parser.parse_args(argv)
    scorer = _StubScorer(scores)
    preds = _make_predictions(n_items)
    exit_code = asyncio.run(eval_ragas.run(args, scorer=scorer, predictions=preds))
    return exit_code, out


def test_run_passes_when_gates_clear(tmp_path: Path) -> None:
    code, out = _run_with_scorer(
        tmp_path,
        {
            "faithfulness": 0.95,
            "answer_relevancy": 0.90,
            "context_precision": 0.7,
            "context_recall": 0.7,
        },
    )
    assert code == 0
    data = json.loads(out.read_text())
    assert data["pass_gates"] is True


def test_run_fails_when_faithfulness_low(tmp_path: Path) -> None:
    code, out = _run_with_scorer(
        tmp_path,
        {
            "faithfulness": 0.10,
            "answer_relevancy": 0.95,
            "context_precision": 0.7,
            "context_recall": 0.7,
        },
    )
    assert code == 1
    data = json.loads(out.read_text())
    assert data["pass_gates"] is False
    assert data["metrics"]["faithfulness"] == pytest.approx(0.10)


def test_run_clamps_out_of_range_scores(tmp_path: Path) -> None:
    """A buggy scorer returning >1.0 must be clamped, not poison the gate."""
    code, out = _run_with_scorer(
        tmp_path,
        {
            "faithfulness": 2.5,  # out of range — must be clamped to 1.0
            "answer_relevancy": -0.5,  # must be clamped to 0.0
            "context_precision": 0.7,
            "context_recall": 0.7,
        },
    )
    assert code == 1  # relevancy=0.0 fails gate after clamp
    data = json.loads(out.read_text())
    assert data["metrics"]["faithfulness"] == pytest.approx(1.0)
    assert data["metrics"]["answer_relevancy"] == pytest.approx(0.0)


def test_run_fails_on_empty_dataset(tmp_path: Path) -> None:
    ds = _write_dataset(tmp_path, [])
    out = tmp_path / "report.json"
    parser = eval_ragas.build_parser()
    args = parser.parse_args(
        ["--bot", "bot-x", "--dataset", str(ds), "--output", str(out)]
    )
    code = asyncio.run(
        eval_ragas.run(
            args,
            scorer=_StubScorer({k: 1.0 for k in eval_ragas.ALL_METRICS}),
            predictions=[],
        )
    )
    assert code == 1
    assert not out.exists()  # no report written for empty dataset


def test_run_writes_report_with_per_row_data(tmp_path: Path) -> None:
    code, out = _run_with_scorer(
        tmp_path,
        {
            "faithfulness": 0.9,
            "answer_relevancy": 0.85,
            "context_precision": 0.7,
            "context_recall": 0.7,
        },
        n_items=3,
    )
    assert code == 0
    data = json.loads(out.read_text())
    assert len(data["rows"]) == 3
    for row in data["rows"]:
        assert {"question", "answer", "contexts", "ground_truth"} <= set(row)


def test_run_honors_custom_gate_overrides(tmp_path: Path) -> None:
    # With permissive gates, even mediocre scores pass.
    code, _out = _run_with_scorer(
        tmp_path,
        {
            "faithfulness": 0.50,
            "answer_relevancy": 0.50,
            "context_precision": 0.5,
            "context_recall": 0.5,
        },
        extra_args=["--faithfulness-gate", "0.40", "--relevancy-gate", "0.40"],
    )
    assert code == 0


def test_run_scorer_called_with_expected_row_shape(tmp_path: Path) -> None:
    """The scorer Port must receive rows shaped for RAGAS (question,
    answer, contexts, ground_truth)."""
    scorer = _StubScorer(
        {k: 1.0 for k in eval_ragas.ALL_METRICS}
    )
    items = [
        {
            "question": "Q1",
            "ground_truth_answer": "A1",
            "ground_truth_contexts": ["c1"],
        }
    ]
    ds = _write_dataset(tmp_path, items)
    out = tmp_path / "report.json"
    args = eval_ragas.build_parser().parse_args(
        ["--bot", "bot-x", "--dataset", str(ds), "--output", str(out)]
    )
    preds = _make_predictions(1)
    code = asyncio.run(eval_ragas.run(args, scorer=scorer, predictions=preds))
    assert code == 0
    assert len(scorer.calls) == 1
    row = scorer.calls[0][0]
    assert set(row.keys()) >= {"question", "answer", "contexts", "ground_truth"}


# ───────────────────────── Dataset coverage check ───────────────────────


def test_all_three_shipped_datasets_load_and_have_30_items() -> None:
    """The 3 shipped golden datasets must parse and contain exactly 30 Q."""
    for name in (
        "30Q_golden_legalbot.json",
        "30Q_golden_medispa.json",
        "30Q_golden_thongtu.json",
    ):
        path = _REPO_ROOT / "tests" / "eval" / "datasets" / name
        assert path.is_file(), f"missing dataset {name}"
        items = eval_ragas.load_dataset(path)
        assert len(items) == 30, f"{name} has {len(items)} items (want 30)"
