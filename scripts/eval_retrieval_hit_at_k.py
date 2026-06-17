#!/usr/bin/env python3
"""Hit@K + nDCG@K retrieval eval framework (T2-Eval).

Compute standard IR metrics (Hit@1/3/5/10 + nDCG@5/10 + MRR) per bot from
golden questions with expected document IDs, emit JSON + a markdown
report. Domain-neutral: knows nothing about industry / brand / customer.

Metric definitions (standard IR — see Manning et al. *Introduction to
Information Retrieval*, Cambridge 2008, ch. 8 + Järvelin & Kekäläinen
"Cumulated Gain-based Evaluation of IR Techniques" ACM TOIS 2002):

  * **Hit@K** — fraction of queries where at least one expected document
    appears in the top-K retrieved list (a.k.a. *recall@K* under the
    convention that each query has at least one relevant doc).
  * **nDCG@K** — discounted cumulative gain normalised by the ideal
    ordering. DCG@K = sum_{i=1..K} rel_i / log2(i + 1); IDCG@K = DCG@K of
    the ideal ranking. nDCG@K = DCG@K / IDCG@K, ∈ [0, 1].
  * **MRR** — mean reciprocal rank, 1/rank_of_first_relevant averaged
    over queries (0 if no relevant doc retrieved).

Benchmark references:
  * NVIDIA NV Answer Accuracy benchmark — IR eval methodology.
  * bangoc123/retrieval-eval-vn (VN legal corpus, Hit@K + MRR).

CLAUDE.md compliance:
  * Zero-hardcode — depths read from `shared.constants` (DB-overrideable
    by callers; CLI accepts `--hit-at-k` / `--ndcg-at-k` overrides for
    one-off explorations).
  * Strategy + DI — retrieval runner is an injected callable
    (`RetrievalRunner` type). Production CI plugs an HTTP/DB-backed
    impl; unit tests inject a deterministic stub.
  * Domain-neutral — golden fixtures live in `tests/fixtures/
    golden_queries/<record_bot_id>.jsonl`; the script never reads brand
    literal.
  * App-does-not-inject — the script computes metrics from retrieval
    output; it never overrides retrieval ordering or fabricates hits.
  * HALLU=0 — IDCG handles zero-relevant queries by returning nDCG=0.0
    (no fabricated lift).

Usage::

    python scripts/eval_retrieval_hit_at_k.py \\
        --golden-dir tests/fixtures/golden_queries \\
        --output-json reports/eval_retrieval_2026-05-13.json \\
        --output-md reports/eval_retrieval_2026-05-13.md

Telemetry: every per-bot scoring pass emits a structlog event
`step_name="eval_retrieval_hit_at_k"` with bot id, query count, and the
computed metric dict so operators can wire alerting on regressions.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Allow direct ``python scripts/eval_retrieval_hit_at_k.py`` invocation from
# any worktree. Mirrors the bootstrap used by `scripts/evaluate_embeddings.py`
# and friends — keeps the script runnable without requiring an editable
# install that points at this exact source tree.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import structlog  # noqa: E402 — after sys.path bootstrap

from ragbot.shared.constants import (  # noqa: E402 — after sys.path bootstrap
    DEFAULT_EVAL_RETRIEVAL_TOP_K,
    DEFAULT_HIT_AT_K_DEPTHS,
    DEFAULT_NDCG_AT_K_DEPTHS,
)

logger = structlog.get_logger("ragbot.eval_retrieval_hit_at_k")

# Suffix used to identify per-bot golden files inside ``--golden-dir``.
GOLDEN_FILE_SUFFIX: str = ".jsonl"

# structlog step name shared with downstream alerting / dashboards.
EVAL_STEP_NAME: str = "eval_retrieval_hit_at_k"


@dataclass(frozen=True)
class GoldenQuery:
    """One golden query with its expected (relevant) document IDs.

    ``expected_doc_ids`` is the unordered set of ground-truth documents
    that *should* be retrieved for ``question``. We accept an iterable
    so callers can pass list/tuple/set; internally we freeze to a tuple
    to keep the dataclass hashable + immutable.

    Domain-neutral: ``record_bot_id`` is an opaque UUID slug; the
    fixture filename is keyed by it. We never look at content.
    """

    question: str
    expected_doc_ids: tuple[str, ...]


@dataclass(frozen=True)
class BotMetrics:
    """IR metrics computed for one bot over its golden query set."""

    record_bot_id: str
    total_queries: int
    hit_at_k: Mapping[int, float] = field(default_factory=dict)
    ndcg_at_k: Mapping[int, float] = field(default_factory=dict)
    mrr: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Stable JSON-serialisable dict (sorted depths for diff-friendly output)."""
        return {
            "record_bot_id": self.record_bot_id,
            "total_queries": self.total_queries,
            "hit_at_k": {str(k): self.hit_at_k[k] for k in sorted(self.hit_at_k)},
            "ndcg_at_k": {str(k): self.ndcg_at_k[k] for k in sorted(self.ndcg_at_k)},
            "mrr": self.mrr,
        }


# A retrieval runner accepts (record_bot_id, question, top_k) and returns
# an *ordered* sequence of retrieved document IDs (most-relevant first).
# Production CI plugs an HTTP/DB-backed callable; unit tests inject a
# deterministic stub.
RetrievalRunner = Callable[[str, str, int], Sequence[str]]


# --------------------------------------------------------------------------- #
# Pure metric computations (no I/O, fully unit-testable).
# --------------------------------------------------------------------------- #


def hit_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """Return 1.0 if any expected id is in top-k retrieved, else 0.0.

    Edge cases:
      * ``k <= 0`` → 0.0 (no retrieval depth = no hit possible).
      * empty ``expected`` → 0.0 (no relevant doc = nothing to hit).
      * empty ``retrieved`` → 0.0.
    """
    if k <= 0 or not expected or not retrieved:
        return 0.0
    expected_set = set(expected)
    top = retrieved[:k]
    return 1.0 if any(doc in expected_set for doc in top) else 0.0


def dcg_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """Binary-relevance DCG@k.

    rel_i ∈ {0, 1} (1 if retrieved[i] is in expected, else 0). DCG =
    sum_{i=1..k} rel_i / log2(i + 1). Position is 1-based per the
    Järvelin/Kekäläinen formulation, hence ``log2(rank + 1)`` where
    ``rank = i + 1`` for 0-based loop index ``i``.
    """
    if k <= 0 or not expected or not retrieved:
        return 0.0
    expected_set = set(expected)
    dcg = 0.0
    for idx, doc in enumerate(retrieved[:k]):
        if doc in expected_set:
            # Position 1 → log2(2) = 1.0 (no penalty); deeper positions
            # discount by log2(rank+1).
            dcg += 1.0 / math.log2(idx + 2)
    return dcg


def ndcg_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """nDCG@k for binary relevance — DCG@k normalised by ideal DCG@k.

    Returns 0.0 when no relevant docs exist (IDCG = 0) to avoid 0/0;
    that matches the HALLU=0 mindset (don't fabricate a lift signal
    when the ground truth is empty).
    """
    if k <= 0 or not expected:
        return 0.0
    dcg = dcg_at_k(retrieved, expected, k)
    # Ideal ranking: all relevant docs at top, up to min(|expected|, k).
    ideal_hits = min(len(set(expected)), k)
    if ideal_hits == 0:
        return 0.0
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def reciprocal_rank(retrieved: Sequence[str], expected: Sequence[str]) -> float:
    """1 / rank-of-first-relevant; 0.0 if no relevant doc retrieved.

    Rank is 1-based per the standard MRR formulation (Voorhees, TREC).
    """
    if not expected or not retrieved:
        return 0.0
    expected_set = set(expected)
    for idx, doc in enumerate(retrieved):
        if doc in expected_set:
            return 1.0 / (idx + 1)
    return 0.0


def compute_bot_metrics(
    record_bot_id: str,
    queries: Sequence[GoldenQuery],
    runner: RetrievalRunner,
    *,
    hit_depths: Sequence[int] = DEFAULT_HIT_AT_K_DEPTHS,
    ndcg_depths: Sequence[int] = DEFAULT_NDCG_AT_K_DEPTHS,
    retrieval_top_k: int = DEFAULT_EVAL_RETRIEVAL_TOP_K,
) -> BotMetrics:
    """Run every query through the runner and average metrics.

    Returns a ``BotMetrics`` even when ``queries`` is empty (zeroed
    metrics — keeps the report stable across empty fixtures).
    """
    if not queries:
        return BotMetrics(
            record_bot_id=record_bot_id,
            total_queries=0,
            hit_at_k={k: 0.0 for k in hit_depths},
            ndcg_at_k={k: 0.0 for k in ndcg_depths},
            mrr=0.0,
        )

    hit_sums: dict[int, float] = {k: 0.0 for k in hit_depths}
    ndcg_sums: dict[int, float] = {k: 0.0 for k in ndcg_depths}
    rr_sum: float = 0.0

    # Cover the deepest measured depth so we never re-call retrieval.
    deepest = max(
        list(hit_depths) + list(ndcg_depths) + [retrieval_top_k],
        default=retrieval_top_k,
    )

    for query in queries:
        try:
            retrieved = runner(record_bot_id, query.question, deepest)
        except (RuntimeError, ValueError, OSError) as exc:
            # Treat runner errors as miss (regression signal) but log
            # so operators see infra issues rather than silent zeros.
            logger.error(
                "eval_retrieval_runner_error",
                step_name=EVAL_STEP_NAME,
                record_bot_id=record_bot_id,
                question=query.question,
                error_type=type(exc).__name__,
            )
            retrieved = ()

        for depth in hit_depths:
            hit_sums[depth] += hit_at_k(retrieved, query.expected_doc_ids, depth)
        for depth in ndcg_depths:
            ndcg_sums[depth] += ndcg_at_k(retrieved, query.expected_doc_ids, depth)
        rr_sum += reciprocal_rank(retrieved, query.expected_doc_ids)

    n = len(queries)
    metrics = BotMetrics(
        record_bot_id=record_bot_id,
        total_queries=n,
        hit_at_k={k: hit_sums[k] / n for k in hit_depths},
        ndcg_at_k={k: ndcg_sums[k] / n for k in ndcg_depths},
        mrr=rr_sum / n,
    )

    logger.info(
        "eval_retrieval_bot_scored",
        step_name=EVAL_STEP_NAME,
        record_bot_id=record_bot_id,
        total_queries=metrics.total_queries,
        hit_at_k={str(k): metrics.hit_at_k[k] for k in hit_depths},
        ndcg_at_k={str(k): metrics.ndcg_at_k[k] for k in ndcg_depths},
        mrr=metrics.mrr,
    )
    return metrics


# --------------------------------------------------------------------------- #
# I/O helpers (small, isolated for test seams).
# --------------------------------------------------------------------------- #


def _load_jsonl(path: Path) -> Iterator[dict[str, object]]:
    """Yield each JSON object from a JSONL file, skipping blank lines."""
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"eval_retrieval_hit_at_k: invalid json at "
                    f"{path}:{line_no}: {exc}"
                ) from exc


def parse_golden_queries(path: Path) -> list[GoldenQuery]:
    """Parse one bot's golden file into ``GoldenQuery`` list.

    Each JSONL line must be an object with keys:
      * ``question`` (str, non-empty)
      * ``expected_doc_ids`` (list[str], at least one entry)

    Extra keys are ignored (forward-compat).
    """
    queries: list[GoldenQuery] = []
    for obj in _load_jsonl(path):
        if not isinstance(obj, dict):
            raise SystemExit(
                f"eval_retrieval_hit_at_k: expected object per line in {path}"
            )
        question = str(obj.get("question") or "").strip()
        if not question:
            raise SystemExit(
                f"eval_retrieval_hit_at_k: missing 'question' in {path}"
            )
        raw_ids = obj.get("expected_doc_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise SystemExit(
                f"eval_retrieval_hit_at_k: 'expected_doc_ids' must be a "
                f"non-empty list in {path} for question={question!r}"
            )
        ids = tuple(str(x) for x in raw_ids if str(x).strip())
        if not ids:
            raise SystemExit(
                f"eval_retrieval_hit_at_k: 'expected_doc_ids' empty after "
                f"strip in {path} for question={question!r}"
            )
        queries.append(GoldenQuery(question=question, expected_doc_ids=ids))
    return queries


def discover_bot_files(golden_dir: Path) -> list[Path]:
    """Return sorted list of per-bot JSONL files in ``golden_dir``.

    Missing dir → empty list (caller treats as "nothing to evaluate").
    """
    if not golden_dir.exists() or not golden_dir.is_dir():
        return []
    return sorted(p for p in golden_dir.iterdir() if p.suffix == GOLDEN_FILE_SUFFIX)


# --------------------------------------------------------------------------- #
# Report rendering.
# --------------------------------------------------------------------------- #


def render_markdown_report(
    results: Sequence[BotMetrics],
    *,
    hit_depths: Sequence[int],
    ndcg_depths: Sequence[int],
) -> str:
    """Render results as a markdown table with one row per bot.

    Format::

        | bot | queries | hit@1 | hit@3 | hit@5 | hit@10 | nDCG@5 | nDCG@10 | MRR |
        |-----|---------|-------|-------|-------|--------|--------|---------|-----|

    Numbers shown to 4 decimal places (enough resolution for IR
    deltas; not so much that noise dominates).
    """
    hit_cols = [f"hit@{k}" for k in hit_depths]
    ndcg_cols = [f"nDCG@{k}" for k in ndcg_depths]
    headers = ["bot", "queries", *hit_cols, *ndcg_cols, "MRR"]
    sep = ["---"] * len(headers)
    lines = [
        "# Retrieval eval — Hit@K + nDCG@K + MRR",
        "",
        "Metric defs: see `scripts/eval_retrieval_hit_at_k.py` docstring.",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for metrics in results:
        row = [
            metrics.record_bot_id,
            str(metrics.total_queries),
            *(f"{metrics.hit_at_k.get(k, 0.0):.4f}" for k in hit_depths),
            *(f"{metrics.ndcg_at_k.get(k, 0.0):.4f}" for k in ndcg_depths),
            f"{metrics.mrr:.4f}",
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return "\n".join(lines)


def render_json_report(
    results: Sequence[BotMetrics],
    *,
    hit_depths: Sequence[int],
    ndcg_depths: Sequence[int],
) -> str:
    """Render results as stable JSON (sorted keys, 2-space indent)."""
    payload = {
        "schema_version": 1,
        "hit_at_k_depths": list(hit_depths),
        "ndcg_at_k_depths": list(ndcg_depths),
        "bots": [m.to_dict() for m in results],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# CLI plumbing.
# --------------------------------------------------------------------------- #


def _stub_runner(record_bot_id: str, question: str, top_k: int) -> Sequence[str]:
    """Default runner — refuses to run because no live transport is wired.

    Production CI must inject a real runner (e.g. HTTP/DB client against
    a local ragbot deployment) by calling ``main(runner=...)``. Without
    one we fail loud rather than silently producing all-zero metrics.
    """
    raise RuntimeError(
        "eval_retrieval_hit_at_k: no retrieval runner injected — CI must "
        f"pass a runner; got call for record_bot_id={record_bot_id!r}, "
        f"question={question!r}, top_k={top_k}"
    )


def _parse_depths(raw: str | None, default: Sequence[int]) -> tuple[int, ...]:
    """Parse comma-separated depth list from CLI (e.g. "1,3,5,10")."""
    if raw is None or not raw.strip():
        return tuple(default)
    try:
        depths = tuple(int(x.strip()) for x in raw.split(",") if x.strip())
    except ValueError as exc:
        raise SystemExit(
            f"eval_retrieval_hit_at_k: invalid depth list {raw!r}: {exc}"
        ) from exc
    if not depths or any(d <= 0 for d in depths):
        raise SystemExit(
            f"eval_retrieval_hit_at_k: depths must be positive ints, got {raw!r}"
        )
    return depths


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Exposed for unit-test inspection."""
    parser = argparse.ArgumentParser(
        prog="eval_retrieval_hit_at_k",
        description=(
            "Compute Hit@K + nDCG@K + MRR per bot from golden retrieval "
            "queries; emit JSON + markdown reports."
        ),
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=Path("tests/fixtures/golden_queries"),
        help=(
            "Directory holding <record_bot_id>.jsonl golden files "
            "(default: tests/fixtures/golden_queries)."
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Path to write JSON report (default: stdout only).",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=None,
        help="Path to write markdown report (default: stdout only).",
    )
    parser.add_argument(
        "--hit-at-k",
        type=str,
        default=None,
        help=(
            "Comma-separated hit@k depths (default: from "
            "DEFAULT_HIT_AT_K_DEPTHS = '1,3,5,10')."
        ),
    )
    parser.add_argument(
        "--ndcg-at-k",
        type=str,
        default=None,
        help=(
            "Comma-separated nDCG@k depths (default: from "
            "DEFAULT_NDCG_AT_K_DEPTHS = '5,10')."
        ),
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=DEFAULT_EVAL_RETRIEVAL_TOP_K,
        help=(
            "Top-K depth fetched per retrieval call (must cover deepest "
            f"hit/nDCG depth; default: {DEFAULT_EVAL_RETRIEVAL_TOP_K})."
        ),
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: RetrievalRunner | None = None,
) -> int:
    """Entry point. Returns exit code for ``sys.exit()``.

    ``runner`` is injected for testability; production CI passes an
    HTTP/DB-backed callable, unit tests pass a deterministic mock.
    """
    args = build_arg_parser().parse_args(argv)
    retrieval_runner: RetrievalRunner = runner if runner is not None else _stub_runner

    hit_depths = _parse_depths(args.hit_at_k, DEFAULT_HIT_AT_K_DEPTHS)
    ndcg_depths = _parse_depths(args.ndcg_at_k, DEFAULT_NDCG_AT_K_DEPTHS)

    files = discover_bot_files(args.golden_dir)
    if not files:
        logger.warning(
            "eval_retrieval_no_golden",
            step_name=EVAL_STEP_NAME,
            golden_dir=str(args.golden_dir),
        )
        return 0

    results: list[BotMetrics] = []
    for bot_file in files:
        record_bot_id = bot_file.stem
        queries = parse_golden_queries(bot_file)
        metrics = compute_bot_metrics(
            record_bot_id,
            queries,
            retrieval_runner,
            hit_depths=hit_depths,
            ndcg_depths=ndcg_depths,
            retrieval_top_k=args.retrieval_top_k,
        )
        results.append(metrics)

    json_blob = render_json_report(
        results, hit_depths=hit_depths, ndcg_depths=ndcg_depths
    )
    md_blob = render_markdown_report(
        results, hit_depths=hit_depths, ndcg_depths=ndcg_depths
    )

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json_blob, encoding="utf-8")
        logger.info(
            "eval_retrieval_wrote_json",
            step_name=EVAL_STEP_NAME,
            path=str(args.output_json),
            bot_count=len(results),
        )
    else:
        sys.stdout.write(json_blob)
        sys.stdout.write("\n")

    if args.output_md is not None:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md_blob, encoding="utf-8")
        logger.info(
            "eval_retrieval_wrote_md",
            step_name=EVAL_STEP_NAME,
            path=str(args.output_md),
            bot_count=len(results),
        )
    else:
        sys.stdout.write(md_blob)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
