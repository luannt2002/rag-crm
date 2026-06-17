"""Mini 30Q legalbot load-test — admin merge HALLU gate.

Thin wrapper around ``loadtest_smartness_300q.run_fixture`` that targets the
30Q legalbot fixture (``reports/legalbot_30q_fixture_v2.json``).

Used by the Master-of-Master merge pipeline as the post-merge HALLU sacred
gate: any HALLU_BREACH > baseline triggers rollback consideration.

Output:
    reports/LEGALBOT_30Q_<ts>/results.json  — full per-turn + summary
    stdout — terse PASS / HALLU breach count, exit non-zero if HALLU > 0
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.loadtest_smartness_300q import run_fixture  # noqa: E402
from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
)

DEFAULT_FIXTURE = "reports/legalbot_30q_fixture_v2.json"


async def main_async() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=DEFAULT_FIXTURE)
    ap.add_argument("--output", default=None)
    ap.add_argument(
        "--pace",
        type=float,
        default=DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
    )
    ap.add_argument(
        "--label",
        default=f"legalbot-30q-{time.strftime('%Y%m%d_%H%M%S')}",
    )
    ap.add_argument(
        "--hallu-baseline",
        type=int,
        default=1,
        help=(
            "HALLU breach baseline (pre-merge known state). Exit non-zero "
            "only if observed breaches strictly exceed this. Default 1 "
            "(Q24 known fabricate, per STATE_SNAPSHOT.md)."
        ),
    )
    args = ap.parse_args()

    fixture = Path(args.questions)
    if not fixture.exists():
        print(f"FIXTURE NOT FOUND: {fixture}", file=sys.stderr)
        return 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else Path(
        f"reports/LEGALBOT_30Q_{ts}/results.json"
    )

    summary = await run_fixture(
        fixture, pace_s=args.pace, out_path=out, label=args.label,
    )

    print("")
    print("=" * 50)
    print(f"LEGALBOT 30Q LOADTEST — {args.label}")
    print("=" * 50)
    print(f"Total:           {summary['total']}")
    print(f"Verdict counts:  {summary['verdict_counts']}")
    print(f"HALLU breach:    {summary['hallu_breach']}/{summary['hallu_trap_total']}"
          f"  (baseline={args.hallu_baseline})")
    print(f"Answered pass:   {summary['answered_pass']}/{summary['non_trap_total']}"
          f"  ({summary['answered_pass_rate']}%)")
    print(f"Refuse gap:      {summary['refuse_gap']}")
    print(f"p50 latency:     {summary['p50_latency_ms']} ms")
    print(f"p95 latency:     {summary['p95_latency_ms']} ms")
    print(f"avg cost/turn:   ${summary['avg_cost_usd']:.6f}")
    print(f"Output:          {out}")
    print("=" * 50)

    if summary["hallu_breach"] > args.hallu_baseline:
        print(
            f"❌ HALLU REGRESSION: {summary['hallu_breach']} > "
            f"baseline {args.hallu_baseline} — rollback consideration",
            file=sys.stderr,
        )
        return 1
    if summary["hallu_breach"] == args.hallu_baseline and args.hallu_baseline > 0:
        print(
            f"⚠️  HALLU = baseline ({args.hallu_baseline}). Sacred=0 still broken, "
            "but no regression vs pre-merge.",
        )
    else:
        print("✅ HALLU sacred gate: no regression vs baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
