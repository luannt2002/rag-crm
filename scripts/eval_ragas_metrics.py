#!/usr/bin/env python3
"""RAGAS metric eval CLI — offline dev tool, NOT chat hot path.

Reads a JSONL of questions, a JSONL of answers, and a corpus directory of
context snippets, then emits a markdown summary with the four RAGAS
metrics (faithfulness, answer_relevancy, context_precision, context_recall).

Today this calls a deterministic stub adapter (see
``ragbot.application.services.ragas_metric_adapter``); admin swaps in the
real ``ragas`` package later via Strategy + Registry. The CLI shape and
exit-code contract are stable.

Exit codes:
    0 — every metric meets its threshold gate.
    1 — at least one metric fell below its threshold OR input I/O failed.
    2 — argparse usage error (handled by argparse itself).

Usage:
    python scripts/eval_ragas_metrics.py \\
        --questions data/q.jsonl \\
        --answers data/a.jsonl \\
        --corpus data/corpus/ \\
        [--min-faithfulness 0.8] \\
        [--min-answer-relevancy 0.7] \\
        [--min-context-precision 0.7] \\
        [--min-context-recall 0.7]

Each ``questions`` line: ``{"id": "...", "question": "..."}``.
Each ``answers`` line: ``{"id": "...", "answer": "...", "contexts": [...]}``.
``contexts`` is optional; when absent, the corpus dir is scanned for
``<id>.txt`` and used as the single context.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final, TextIO

from ragbot.application.services.ragas_metric_adapter import (
    EXPECTED_METRIC_KEYS,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
    METRIC_FAITHFULNESS,
    RagasMetricAdapter,
    RagasMetricPort,
)
from ragbot.shared.constants import (
    DEFAULT_RAGAS_MIN_ANSWER_RELEVANCY,
    DEFAULT_RAGAS_MIN_CONTEXT_PRECISION,
    DEFAULT_RAGAS_MIN_CONTEXT_RECALL,
    DEFAULT_RAGAS_MIN_FAITHFULNESS,
)

EXIT_OK: Final[int] = 0
EXIT_FAIL: Final[int] = 1


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Load a JSONL file into a list of dicts (small files only)."""
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_no} not valid JSON: {exc.msg}"
                ) from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_no} not a JSON object")
            rows.append(obj)
    return rows


def _load_corpus_context(corpus_dir: Path, qid: str) -> list[str]:
    """Return ``<corpus>/<qid>.txt`` content as a single-element context list.

    Empty list when the file is absent — adapter will then collapse
    faithfulness to 0.0, exposing the missing-retrieval failure.
    """
    candidate = corpus_dir / f"{qid}.txt"
    if not candidate.is_file():
        return []
    return [candidate.read_text(encoding="utf-8")]


def _aggregate(
    rows: list[dict[str, float]],
) -> dict[str, float]:
    """Mean per metric across ``rows`` (empty rows -> 0.0 for every metric)."""
    if not rows:
        return dict.fromkeys(EXPECTED_METRIC_KEYS, 0.0)
    totals: dict[str, float] = dict.fromkeys(EXPECTED_METRIC_KEYS, 0.0)
    for row in rows:
        for key in EXPECTED_METRIC_KEYS:
            totals[key] += float(row.get(key, 0.0))
    n = float(len(rows))
    return {key: totals[key] / n for key in EXPECTED_METRIC_KEYS}


def _format_markdown(aggregate: dict[str, float], n: int) -> str:
    """Render aggregate metrics as a small markdown table."""
    header = "| metric | mean | n |\n|---|---|---|"
    lines = [header]
    for key in EXPECTED_METRIC_KEYS:
        lines.append(f"| {key} | {aggregate[key]:.4f} | {n} |")
    return "\n".join(lines)


def _gate(
    aggregate: dict[str, float],
    *,
    min_faithfulness: float,
    min_answer_relevancy: float,
    min_context_precision: float,
    min_context_recall: float,
) -> list[str]:
    """Return the list of metrics that failed their gate."""
    failures: list[str] = []
    if aggregate[METRIC_FAITHFULNESS] < min_faithfulness:
        failures.append(METRIC_FAITHFULNESS)
    if aggregate[METRIC_ANSWER_RELEVANCY] < min_answer_relevancy:
        failures.append(METRIC_ANSWER_RELEVANCY)
    if aggregate[METRIC_CONTEXT_PRECISION] < min_context_precision:
        failures.append(METRIC_CONTEXT_PRECISION)
    if aggregate[METRIC_CONTEXT_RECALL] < min_context_recall:
        failures.append(METRIC_CONTEXT_RECALL)
    return failures


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser (exposed for tests)."""
    parser = argparse.ArgumentParser(
        prog="eval_ragas_metrics",
        description="Offline RAGAS metric eval scaffold (stub adapter).",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        required=True,
        help="JSONL with {id, question} per line.",
    )
    parser.add_argument(
        "--answers",
        type=Path,
        required=True,
        help="JSONL with {id, answer, contexts?} per line.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        required=True,
        help="Directory containing <id>.txt fallback contexts.",
    )
    parser.add_argument(
        "--min-faithfulness",
        type=float,
        default=DEFAULT_RAGAS_MIN_FAITHFULNESS,
    )
    parser.add_argument(
        "--min-answer-relevancy",
        type=float,
        default=DEFAULT_RAGAS_MIN_ANSWER_RELEVANCY,
    )
    parser.add_argument(
        "--min-context-precision",
        type=float,
        default=DEFAULT_RAGAS_MIN_CONTEXT_PRECISION,
    )
    parser.add_argument(
        "--min-context-recall",
        type=float,
        default=DEFAULT_RAGAS_MIN_CONTEXT_RECALL,
    )
    return parser


def run(
    args: argparse.Namespace,
    *,
    adapter: RagasMetricPort | None = None,
    out_stream: TextIO | None = None,
) -> int:
    """Execute the eval. Returns the process exit code.

    ``adapter`` is injected for tests; production calls construct the
    deterministic stub.
    """
    metric_adapter: RagasMetricPort = adapter if adapter is not None else RagasMetricAdapter()
    stream: TextIO = out_stream if out_stream is not None else sys.stdout

    try:
        questions = _read_jsonl(args.questions)
        answers = _read_jsonl(args.answers)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FAIL

    answers_by_id: dict[str, dict[str, object]] = {}
    for row in answers:
        rid = row.get("id")
        if isinstance(rid, str):
            answers_by_id[rid] = row

    per_turn: list[dict[str, float]] = []
    for q_row in questions:
        qid_obj = q_row.get("id")
        question_obj = q_row.get("question", "")
        if not isinstance(qid_obj, str) or not isinstance(question_obj, str):
            continue
        a_row = answers_by_id.get(qid_obj, {})
        answer_obj = a_row.get("answer", "")
        contexts_obj = a_row.get("contexts")
        contexts: list[str]
        if isinstance(contexts_obj, list):
            contexts = [c for c in contexts_obj if isinstance(c, str)]
        else:
            contexts = _load_corpus_context(args.corpus, qid_obj)
        if not isinstance(answer_obj, str):
            answer_obj = ""
        per_turn.append(
            metric_adapter.score(question_obj, answer_obj, contexts)
        )

    aggregate = _aggregate(per_turn)
    print(_format_markdown(aggregate, len(per_turn)), file=stream)

    failures = _gate(
        aggregate,
        min_faithfulness=args.min_faithfulness,
        min_answer_relevancy=args.min_answer_relevancy,
        min_context_precision=args.min_context_precision,
        min_context_recall=args.min_context_recall,
    )
    if failures:
        print(
            "FAIL: metrics below threshold: " + ", ".join(failures),
            file=sys.stderr,
        )
        return EXIT_FAIL
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry. ``argv`` overrides ``sys.argv[1:]`` for tests."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
