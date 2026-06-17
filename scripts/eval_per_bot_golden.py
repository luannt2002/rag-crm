"""Per-bot golden test eval CLI.

Reads one ``<record_bot_id>.jsonl`` per bot from ``--golden-dir`` (each line
a Q&A entry: ``{question, expected_answer, expected_intent, must_cite}``),
runs each question through a bot-runner callable, computes per-bot pass
rate, then compares to a baseline file. Exits 1 if any bot regresses
below its baseline pass rate (optionally allowing ``--tolerance``).

Domain-neutral: knows nothing about industry / brand. The ``record_bot_id``
filename slug is opaque; the platform's mandatory 4-key identity tuple
``(record_tenant_id, workspace_id, bot_id, channel_type)`` resolves to a
single ``record_bot_id`` UUID, and the eval keys golden files by that UUID.

The CLI is import-safe (parsing & evaluation logic is isolated in pure
functions) so unit tests can drive it with a mock bot-runner without
spinning Postgres/Redis. The default bot-runner stub raises if invoked
without a real implementation injected — CI plugs an HTTP-backed runner
into ``main()``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Floating-point slack when comparing current vs baseline pass rate.
# 0.0 = strict regression check (any drop fails). Operators may relax via
# ``--tolerance`` (e.g. 0.02 for "allow 2 percentage point noise").
DEFAULT_REGRESSION_TOLERANCE: float = 0.0

# Suffix used to identify per-bot golden files inside ``--golden-dir``.
GOLDEN_FILE_SUFFIX: str = ".jsonl"


@dataclass(frozen=True)
class GoldenEntry:
    """One Q&A pair lifted from a per-bot golden JSONL line."""

    question: str
    expected_answer: str
    expected_intent: str
    must_cite: bool


@dataclass(frozen=True)
class BotResult:
    """Outcome of running every golden entry for one bot."""

    record_bot_id: str
    total: int
    passed: int

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.passed / self.total


# A bot runner takes (record_bot_id, question) and returns the bot
# response dict. Production CI plugs an HTTP-backed callable in main();
# unit tests inject a deterministic stub.
BotRunner = Callable[[str, str], Mapping[str, object]]


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
                    f"eval_per_bot_golden: invalid json at "
                    f"{path}:{line_no}: {exc}"
                ) from exc


def parse_golden_entries(path: Path) -> list[GoldenEntry]:
    """Parse one bot's JSONL file into a list of ``GoldenEntry``.

    Missing optional fields default to empty / False so a bot owner can
    add fields incrementally without breaking older lines.
    """
    entries: list[GoldenEntry] = []
    for obj in _load_jsonl(path):
        if not isinstance(obj, dict):
            raise SystemExit(
                f"eval_per_bot_golden: expected object per line in {path}"
            )
        question = str(obj.get("question") or "").strip()
        if not question:
            raise SystemExit(
                f"eval_per_bot_golden: missing 'question' in {path}"
            )
        entries.append(
            GoldenEntry(
                question=question,
                expected_answer=str(obj.get("expected_answer") or ""),
                expected_intent=str(obj.get("expected_intent") or ""),
                must_cite=bool(obj.get("must_cite", False)),
            )
        )
    return entries


def discover_bot_files(golden_dir: Path) -> list[Path]:
    """Return sorted list of per-bot JSONL files in ``golden_dir``.

    Empty / missing dir is a no-op (returns []), the caller treats that
    as "nothing to evaluate" and exits cleanly.
    """
    if not golden_dir.exists() or not golden_dir.is_dir():
        return []
    return sorted(p for p in golden_dir.iterdir() if p.suffix == GOLDEN_FILE_SUFFIX)


def evaluate_entry(entry: GoldenEntry, response: Mapping[str, object]) -> bool:
    """Return True if the bot response satisfies the entry's rubric.

    Rubric (all must hold):
      1. ``expected_answer`` substring appears in answer (case-insensitive).
         Empty expected_answer auto-passes this check.
      2. If ``expected_intent`` non-empty, the response's ``intent`` must
         equal it (case-insensitive).
      3. If ``must_cite`` is True, response must carry at least one
         non-empty citation in ``citations``.
    """
    answer = str(response.get("answer") or "").lower()
    expected = entry.expected_answer.lower()
    if expected and expected not in answer:
        return False

    if entry.expected_intent:
        actual_intent = str(response.get("intent") or "").lower()
        if actual_intent != entry.expected_intent.lower():
            return False

    if entry.must_cite:
        citations = response.get("citations")
        if not isinstance(citations, list) or not any(citations):
            return False

    return True


def run_bot(
    record_bot_id: str,
    entries: list[GoldenEntry],
    runner: BotRunner,
) -> BotResult:
    """Run every entry through the runner and tally pass count."""
    passed = 0
    for entry in entries:
        try:
            response = runner(record_bot_id, entry.question)
        except (RuntimeError, ValueError, OSError) as exc:
            # Treat runner errors as fail (regression signal) but log so
            # operators see infra issues rather than silent zeros.
            logger.error(
                "runner_error record_bot_id=%s question=%r err=%s",
                record_bot_id,
                entry.question,
                exc,
            )
            continue
        if evaluate_entry(entry, response):
            passed += 1
    return BotResult(
        record_bot_id=record_bot_id,
        total=len(entries),
        passed=passed,
    )


def load_baseline(path: Path | None) -> dict[str, float]:
    """Return ``{record_bot_id: baseline_pass_rate}`` mapping.

    Missing file → empty dict (caller treats as "no baseline = treat all
    as new bots, no regression check"). Each baseline JSONL line:
    ``{"record_bot_id": "...", "baseline_pass_rate": 0.85}``.
    """
    if path is None or not path.exists():
        return {}
    baseline: dict[str, float] = {}
    for obj in _load_jsonl(path):
        if not isinstance(obj, dict):
            raise SystemExit(
                f"eval_per_bot_golden: invalid baseline line in {path}"
            )
        bot_id = str(obj.get("record_bot_id") or "").strip()
        if not bot_id:
            raise SystemExit(
                f"eval_per_bot_golden: missing record_bot_id in {path}"
            )
        rate_raw = obj.get("baseline_pass_rate", 0.0)
        if not isinstance(rate_raw, (int, float)):
            raise SystemExit(
                f"eval_per_bot_golden: baseline_pass_rate must be numeric in {path}"
            )
        baseline[bot_id] = float(rate_raw)
    return baseline


def detect_regressions(
    results: list[BotResult],
    baseline: Mapping[str, float],
    tolerance: float,
) -> list[tuple[str, float, float]]:
    """Return list of ``(record_bot_id, current, baseline)`` for regressors.

    A bot regresses when ``current_pass_rate < baseline_pass_rate -
    tolerance``. Bots with no baseline entry are skipped (no comparison
    possible).
    """
    regressions: list[tuple[str, float, float]] = []
    for result in results:
        if result.record_bot_id not in baseline:
            continue
        baseline_rate = baseline[result.record_bot_id]
        if result.pass_rate < baseline_rate - tolerance:
            regressions.append(
                (result.record_bot_id, result.pass_rate, baseline_rate)
            )
    return regressions


def _stub_runner(record_bot_id: str, question: str) -> Mapping[str, object]:
    """Default runner — refuses to run because no live transport is wired.

    Production CI must inject a real runner (e.g. HTTP client against a
    local ragbot deployment) by calling ``main(runner=...)``. Without
    one, fail loud rather than silently returning empty answers.
    """
    raise RuntimeError(
        "eval_per_bot_golden: no bot runner injected — CI must pass "
        f"a runner; got call for record_bot_id={record_bot_id!r}, "
        f"question={question!r}"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Exposed for unit-test inspection."""
    parser = argparse.ArgumentParser(
        prog="eval_per_bot_golden",
        description=(
            "Per-bot golden test eval — runs golden Q&A pairs per bot, "
            "compares pass rate to baseline, exits 1 on regression."
        ),
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=Path("golden_set"),
        help="Directory holding <record_bot_id>.jsonl files (default: golden_set/).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Optional JSONL file with prior pass rates "
            "(default: no baseline = no regression check)."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_REGRESSION_TOLERANCE,
        help=(
            "Allowed slack on pass-rate drop (default: 0.0 = strict). "
            "Example: 0.02 allows a 2 percentage point regression."
        ),
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: BotRunner | None = None,
) -> int:
    """Entry point. Returns exit code for ``sys.exit()``.

    ``runner`` is injected for testability — production CI passes an
    HTTP-backed callable; unit tests pass a deterministic mock.
    """
    args = build_arg_parser().parse_args(argv)
    bot_runner: BotRunner = runner if runner is not None else _stub_runner

    files = discover_bot_files(args.golden_dir)
    if not files:
        logger.info(
            "no golden files found in %s — nothing to evaluate",
            args.golden_dir,
        )
        return 0

    baseline = load_baseline(args.baseline)
    results: list[BotResult] = []
    for bot_file in files:
        record_bot_id = bot_file.stem
        entries = parse_golden_entries(bot_file)
        result = run_bot(record_bot_id, entries, bot_runner)
        results.append(result)
        logger.info(
            "bot=%s total=%d passed=%d pass_rate=%.4f",
            result.record_bot_id,
            result.total,
            result.passed,
            result.pass_rate,
        )

    regressions = detect_regressions(results, baseline, args.tolerance)
    if regressions:
        for bot_id, current, base in regressions:
            logger.error(
                "REGRESSION bot=%s current_pass_rate=%.4f baseline=%.4f",
                bot_id,
                current,
                base,
            )
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.exit(main())
