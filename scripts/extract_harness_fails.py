"""Extract unanswered turns from a harness run JSON for manual review.

Consumes output of scripts/test_rooms_v3.py (reports/test_run_*.json).
A turn is considered a "fail" when its answer_type != "answered"
(greeting, out_of_scope, refuse, blocked, error, etc.).

Usage:
    python scripts/extract_harness_fails.py reports/run.json
    python scripts/extract_harness_fails.py reports/run.json --max 10

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ANSWERED_TYPE = "answered"


def _load(p: Path) -> dict:
    if not p.exists():
        raise SystemExit(f"extract_harness_fails: file not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"extract_harness_fails: invalid json in {p}: {exc}")


def _collect_fails(run: dict) -> list[dict]:
    """Return list of dicts merging room meta + turn for every non-answered turn."""
    out: list[dict] = []
    for room in run.get("rooms", []) or []:
        for turn in room.get("turns", []) or []:
            if turn.get("answer_type") == ANSWERED_TYPE:
                continue
            out.append(
                {
                    "room_id": room.get("room_id"),
                    "topic": room.get("topic"),
                    "turn": turn,
                }
            )
    return out


def _truncate(text: Any, n: int = 500) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\n", " ").strip()
    if len(s) > n:
        return s[: n - 3] + "..."
    return s


def _fmt_sources(sources: Any) -> str:
    if not sources:
        return "[]"
    if isinstance(sources, (list, tuple)):
        items = [str(s) for s in sources[:5]]
        suffix = f", ... (+{len(sources) - 5} more)" if len(sources) > 5 else ""
        return "[" + ", ".join(items) + suffix + "]"
    return str(sources)


def _format_fail(idx: int, total: int, fail: dict) -> str:
    turn = fail["turn"]
    debug = turn.get("debug") or {}
    tokens = turn.get("tokens") or {}
    crag = turn.get("crag") or debug.get("crag") or {}
    expected = turn.get("expected_sources") or turn.get("expected") or debug.get("expected_sources")

    duration_ms = float(turn.get("duration_ms") or 0.0)
    retrieve_ms = float(turn.get("retrieve_ms") or debug.get("retrieve_ms") or 0.0)
    generate_ms = float(turn.get("generate_ms") or debug.get("generate_ms") or 0.0)

    category = turn.get("category") or debug.get("category") or fail.get("topic") or "-"
    difficulty = turn.get("difficulty") or debug.get("difficulty") or debug.get("intent") or "-"

    header = (
        f"[fail {idx}/{total}]  room={fail.get('room_id')} "
        f"question_id={turn.get('_idx')} category={category} difficulty={difficulty} "
        f"answer_type={turn.get('answer_type')}"
    )

    lines = [
        header,
        f"  prompt:      {_truncate(turn.get('_question') or turn.get('prompt'), 400)}",
        f"  answer:      {_truncate(turn.get('answer'), 400) or '<empty>'}",
        f"  reason:      {_truncate(turn.get('answer_reason'), 200) or '-'}",
        (
            f"  retrieval:   top1_score={float(turn.get('top_score') or 0.0):.4f} "
            f"count={turn.get('chunks_used', 0)}  "
            f"sources={_fmt_sources(turn.get('sources'))}"
        ),
    ]

    if crag:
        lines.append(
            "  crag:        "
            f"relevant={crag.get('relevant', '-')} "
            f"ambiguous={crag.get('ambiguous', '-')} "
            f"irrelevant={crag.get('irrelevant', '-')}  "
            f"decision={crag.get('decision', '-')}"
        )

    timing_bits = [f"total={duration_ms / 1000.0:.2f}s"]
    if retrieve_ms:
        timing_bits.append(f"retrieve={retrieve_ms / 1000.0:.2f}s")
    if generate_ms:
        timing_bits.append(f"generate={generate_ms / 1000.0:.2f}s")
    lines.append("  timing:      " + " ".join(timing_bits))

    if tokens:
        lines.append(
            "  tokens:      "
            f"prompt={tokens.get('prompt', 0)} "
            f"completion={tokens.get('completion', 0)} "
            f"cached={tokens.get('cached', 0)}  "
            f"cost=${float(turn.get('cost_usd') or 0.0):.4f}"
        )

    if debug.get("intent"):
        lines.append(
            f"  debug:       intent={debug.get('intent')} "
            f"model={debug.get('model', '-')} "
            f"source={debug.get('source', '-')}"
        )

    if expected:
        lines.append(f"  expected:    {_fmt_sources(expected)}")

    lines.append("  --")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump unanswered harness turns.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--max", type=int, default=20, dest="max_n")
    args = parser.parse_args()

    run = _load(args.path)
    fails = _collect_fails(run)
    total = len(fails)

    if not total:
        print(f"# no failed turns in {args.path}")
        return 0

    shown = min(total, args.max_n) if args.max_n and args.max_n > 0 else total
    print(f"# {total} failed turn(s) in {args.path}; showing {shown}")
    print()
    for i, fail in enumerate(fails[:shown], start=1):
        print(_format_fail(i, total, fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
