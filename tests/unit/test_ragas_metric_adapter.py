"""Unit coverage for the RAGAS metric adapter scaffold + CLI gate.

Mock-only — no live LLM, no `ragas` package import. Pins the four-key
output contract (real provider must preserve), the empty-context
faithfulness collapse (no contexts -> 0.0), the CLI entry shape, and the
threshold-gate exit-code path.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

from ragbot.application.services.ragas_metric_adapter import (
    EXPECTED_METRIC_KEYS,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
    METRIC_FAITHFULNESS,
    RagasMetricAdapter,
)


def _load_cli_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "eval_ragas_metrics.py"
    if not script_path.is_file():
        raise FileNotFoundError(f"CLI script missing: {script_path}")
    spec = importlib.util.spec_from_file_location(
        "_eval_ragas_metrics", script_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {script_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_empty_contexts_force_faithfulness_zero() -> None:
    """No contexts means nothing to ground against -> faithfulness MUST be 0.0."""
    adapter = RagasMetricAdapter(stub_score=0.9)
    out = adapter.score("Câu hỏi gì?", "Câu trả lời.", [])
    assert out[METRIC_FAITHFULNESS] == 0.0
    # Other metrics still reflect the stub score (the empty-context rule
    # is faithfulness-specific, not a global zero).
    assert out[METRIC_ANSWER_RELEVANCY] == 0.9
    assert out[METRIC_CONTEXT_PRECISION] == 0.9
    assert out[METRIC_CONTEXT_RECALL] == 0.9


def test_stub_returns_all_four_expected_keys() -> None:
    """Adapter MUST return exactly the four contract keys, each in [0,1]."""
    adapter = RagasMetricAdapter(stub_score=0.5)
    out = adapter.score("Q", "A", ["ctx-1", "ctx-2"])
    assert set(out.keys()) == set(EXPECTED_METRIC_KEYS)
    assert len(out) == len(EXPECTED_METRIC_KEYS)
    for key in EXPECTED_METRIC_KEYS:
        value = out[key]
        assert 0.0 <= value <= 1.0, f"{key}={value} out of [0,1]"
    assert out[METRIC_FAITHFULNESS] == 0.5


def test_cli_entry_callable_and_passes_when_threshold_below_score(
    tmp_path: Path,
) -> None:
    """`main(argv=[...])` is callable and returns 0 when gates are loose."""
    cli = _load_cli_module()

    questions = tmp_path / "q.jsonl"
    answers = tmp_path / "a.jsonl"
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    questions.write_text(
        json.dumps({"id": "q1", "question": "What?"}) + "\n",
        encoding="utf-8",
    )
    answers.write_text(
        json.dumps(
            {"id": "q1", "answer": "Because.", "contexts": ["ctx-text"]}
        )
        + "\n",
        encoding="utf-8",
    )

    # Stub score = 0.5; lower every gate to 0.0 so the run passes.
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "--questions",
            str(questions),
            "--answers",
            str(answers),
            "--corpus",
            str(corpus),
            "--min-faithfulness",
            "0.0",
            "--min-answer-relevancy",
            "0.0",
            "--min-context-precision",
            "0.0",
            "--min-context-recall",
            "0.0",
        ]
    )
    buf = io.StringIO()
    rc = cli.run(
        args, adapter=RagasMetricAdapter(stub_score=0.5), out_stream=buf
    )
    assert rc == cli.EXIT_OK
    rendered = buf.getvalue()
    # Markdown table MUST list every metric key exactly once.
    for key in EXPECTED_METRIC_KEYS:
        assert rendered.count(f"| {key} |") == 1


def test_cli_threshold_gate_fails_when_score_below_min(tmp_path: Path) -> None:
    """Stub score below ``--min-faithfulness`` MUST exit 1 (gate trips)."""
    cli = _load_cli_module()

    questions = tmp_path / "q.jsonl"
    answers = tmp_path / "a.jsonl"
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    questions.write_text(
        json.dumps({"id": "q1", "question": "What?"}) + "\n",
        encoding="utf-8",
    )
    answers.write_text(
        json.dumps(
            {"id": "q1", "answer": "Because.", "contexts": ["ctx-text"]}
        )
        + "\n",
        encoding="utf-8",
    )

    args = argparse.Namespace(
        questions=questions,
        answers=answers,
        corpus=corpus,
        min_faithfulness=0.99,
        min_answer_relevancy=0.0,
        min_context_precision=0.0,
        min_context_recall=0.0,
    )
    buf = io.StringIO()
    # Stub returns 0.5 < 0.99 -> faithfulness gate trips -> exit 1.
    rc = cli.run(
        args, adapter=RagasMetricAdapter(stub_score=0.5), out_stream=buf
    )
    assert rc == cli.EXIT_FAIL
