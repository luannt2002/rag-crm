"""Eval diff: compare two harness run JSONs and print per-bucket delta.

Consumes output from scripts/test_rooms_v3.py (reports/test_run_*.json).

Usage:
    python scripts/eval_diff.py --baseline reports/A.json --current reports/B.json
    python scripts/eval_diff.py --baseline A.json --current B.json --by category
    python scripts/eval_diff.py --baseline A.json --current B.json --by difficulty

Buckets:
    room       -> group by room_id (default)
    category   -> group by room.topic (harness has no 'category' field; topic
                  is the closest proxy)
    difficulty -> group by turn.debug.intent (factoid/procedural/etc.) as a
                  proxy for difficulty until the harness emits a real field

Stdlib only (argparse + json + statistics).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ANSWERED_TYPE = "answered"


def _load(p: Path) -> dict:
    if not p.exists():
        raise SystemExit(f"eval_diff: file not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"eval_diff: invalid json in {p}: {exc}")


def _iter_turns(run: dict) -> list[tuple[dict, dict]]:
    """Yield (room, turn) pairs. Returns list so it is reusable."""
    pairs: list[tuple[dict, dict]] = []
    for room in run.get("rooms", []) or []:
        for turn in room.get("turns", []) or []:
            pairs.append((room, turn))
    return pairs


def _bucket_key(room: dict, turn: dict, by: str) -> str:
    if by == "room":
        return str(room.get("room_id") or "unknown")
    if by == "category":
        return str(room.get("topic") or room.get("category") or "unknown")
    if by == "difficulty":
        debug = turn.get("debug") or {}
        return str(
            turn.get("difficulty")
            or debug.get("difficulty")
            or debug.get("intent")
            or "unknown"
        )
    return "unknown"


def _safe_mean(values: list[float]) -> float:
    vals = [v for v in values if v is not None]
    return mean(vals) if vals else 0.0


def _bucketed_stats(run: dict, by: str) -> dict[str, dict[str, float]]:
    """Produce {bucket_key: {answered_rate, avg_top_score, avg_latency_ms,
    cost_per_answered, total_turns, answered_turns}}.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for room, turn in _iter_turns(run):
        grouped[_bucket_key(room, turn, by)].append(turn)

    out: dict[str, dict[str, float]] = {}
    for bucket, turns in grouped.items():
        total = len(turns)
        answered = [t for t in turns if t.get("answer_type") == ANSWERED_TYPE]
        n_answered = len(answered)
        top_scores = [float(t.get("top_score") or 0.0) for t in answered]
        latencies = [float(t.get("duration_ms") or 0.0) for t in turns]
        costs = [float(t.get("cost_usd") or 0.0) for t in answered]
        cost_per_ans = (sum(costs) / n_answered) if n_answered else 0.0
        out[bucket] = {
            "total_turns": float(total),
            "answered_turns": float(n_answered),
            "answered_rate": (n_answered / total) if total else 0.0,
            "avg_top_score": _safe_mean(top_scores),
            "avg_latency_ms": _safe_mean(latencies),
            "cost_per_answered": cost_per_ans,
        }
    return out


def _overall(run: dict) -> dict[str, float]:
    turns = [t for _, t in _iter_turns(run)]
    total = len(turns)
    answered = [t for t in turns if t.get("answer_type") == ANSWERED_TYPE]
    n_answered = len(answered)
    top_scores = [float(t.get("top_score") or 0.0) for t in answered]
    latencies = [float(t.get("duration_ms") or 0.0) for t in turns]
    costs = [float(t.get("cost_usd") or 0.0) for t in answered]
    return {
        "total_turns": float(total),
        "answered_turns": float(n_answered),
        "answered_rate": (n_answered / total) if total else 0.0,
        "avg_top_score": _safe_mean(top_scores),
        "avg_latency_ms": _safe_mean(latencies),
        "cost_per_answered": (sum(costs) / n_answered) if n_answered else 0.0,
    }


def _fmt_pct(v: float) -> str:
    return f"{v * 100:5.1f}%"


def _fmt_score(v: float) -> str:
    return f"{v:6.3f}"


def _fmt_ms(v: float) -> str:
    return f"{v / 1000.0:6.2f}s"


def _fmt_cost(v: float) -> str:
    return f"${v:.4f}"


def _fmt_delta_pp(base: float, curr: float) -> str:
    d = (curr - base) * 100.0
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}pp"


def _fmt_delta_num(base: float, curr: float, fmt: str = "{:+.3f}") -> str:
    return fmt.format(curr - base)


def _print_delta_table(
    base_stats: dict[str, dict[str, float]],
    curr_stats: dict[str, dict[str, float]],
    base_overall: dict[str, float],
    curr_overall: dict[str, float],
    by: str,
) -> None:
    all_keys = sorted(set(base_stats) | set(curr_stats))
    header = (
        f"{'BUCKET (' + by + ')':<34} "
        f"{'base ans%/top/ms/$':<30} "
        f"{'curr ans%/top/ms/$':<30} "
        f"{'delta':<28}"
    )
    print(header)
    print("-" * len(header))

    def _row(label: str, b: dict[str, float] | None, c: dict[str, float] | None) -> None:
        b = b or {"answered_rate": 0.0, "avg_top_score": 0.0, "avg_latency_ms": 0.0, "cost_per_answered": 0.0}
        c = c or {"answered_rate": 0.0, "avg_top_score": 0.0, "avg_latency_ms": 0.0, "cost_per_answered": 0.0}
        base_part = f"{_fmt_pct(b['answered_rate'])} {_fmt_score(b['avg_top_score'])} {_fmt_ms(b['avg_latency_ms'])} {_fmt_cost(b['cost_per_answered'])}"
        curr_part = f"{_fmt_pct(c['answered_rate'])} {_fmt_score(c['avg_top_score'])} {_fmt_ms(c['avg_latency_ms'])} {_fmt_cost(c['cost_per_answered'])}"
        delta_part = (
            f"{_fmt_delta_pp(b['answered_rate'], c['answered_rate'])} "
            f"{_fmt_delta_num(b['avg_top_score'], c['avg_top_score'])} "
            f"{_fmt_delta_num(b['avg_latency_ms'] / 1000.0, c['avg_latency_ms'] / 1000.0, '{:+.2f}s')} "
            f"{_fmt_delta_num(b['cost_per_answered'], c['cost_per_answered'], '{:+.4f}')}"
        )
        label_trim = (label[:31] + "...") if len(label) > 34 else label
        print(f"{label_trim:<34} {base_part:<30} {curr_part:<30} {delta_part:<28}")

    for k in all_keys:
        _row(k, base_stats.get(k), curr_stats.get(k))
    print("-" * len(header))
    _row("OVERALL", base_overall, curr_overall)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff two harness run JSONs.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument(
        "--by",
        default="room",
        choices=["room", "category", "difficulty"],
    )
    args = parser.parse_args()

    base = _load(args.baseline)
    curr = _load(args.current)

    base_stats = _bucketed_stats(base, args.by)
    curr_stats = _bucketed_stats(curr, args.by)
    base_overall = _overall(base)
    curr_overall = _overall(curr)

    print(f"# eval_diff  baseline={args.baseline}  current={args.current}  by={args.by}")
    print(
        f"# baseline: {int(base_overall['total_turns'])} turns, "
        f"{int(base_overall['answered_turns'])} answered"
    )
    print(
        f"# current : {int(curr_overall['total_turns'])} turns, "
        f"{int(curr_overall['answered_turns'])} answered"
    )
    print()
    _print_delta_table(base_stats, curr_stats, base_overall, curr_overall, args.by)
    return 0


if __name__ == "__main__":
    sys.exit(main())
