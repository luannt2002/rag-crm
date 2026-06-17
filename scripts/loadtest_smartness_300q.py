"""Smartness 300Q load-test runner — Phase D Stream D5.

Replays ``tests/loadtest/smartness_300q_fixture.json`` (3 bots × 100Q × 7
patterns) against the running ragbot at ``$RAGBOT_BASE_URL``
(default ``http://localhost:3004``).

Pattern coverage (per bot):
    single_entity, multi_entity, typo_no_diacritic, abbreviation,
    semantic, cross_reference, trap_hallu.

Output: JSON aggregate to ``reports/SMARTNESS_300Q_RESULT_<ts>.json``.
The analyzer (``analyze_smartness_300q.py``) consumes this JSON to produce
a per-bot / per-pattern verdict breakdown.

Admin executes; team coder does NO live execution.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._loadtest_common import is_refuse  # noqa: E402

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
    DEFAULT_LOADTEST_REQUEST_TIMEOUT_S,
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
DEFAULT_FIXTURE = "tests/loadtest/smartness_300q_fixture.json"


def _bypass_headers() -> dict[str, str]:
    token = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    if not token:
        return {}
    return {RAGBOT_LOADTEST_BYPASS_HEADER: token}


async def get_self_token(client: httpx.AsyncClient) -> str:
    """Lift admin self-token for the test/chat endpoint."""
    r = await client.get(
        f"{BASE_URL}/api/ragbot/test/tokens/self",
        headers=_bypass_headers(),
    )
    r.raise_for_status()
    return r.json()["token"]


async def ask_one(
    client: httpx.AsyncClient,
    token: str,
    *,
    bot_id: str,
    workspace_id: str,
    channel_type: str,
    question: str,
    connect_id: str,
) -> dict[str, Any]:
    body = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "workspace_id": workspace_id,
        "question": question,
        "connect_id": connect_id,
        "bypass_cache": True,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        **_bypass_headers(),
    }
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/api/ragbot/test/chat",
            headers=headers,
            json=body,
            timeout=DEFAULT_LOADTEST_REQUEST_TIMEOUT_S,
        )
        r.raise_for_status()
        d = r.json()
        d["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        return d
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }


def classify(answer: str, *, is_trap: bool, expected_verdict: str) -> str:
    """Classify a single answer into one of:

    PASS_ANSWERED, PASS_REFUSED, HALLU_BREACH, REFUSE_GAP, ERR.

    HALLU_BREACH is the sacred breach: trap question got non-refusal.
    REFUSE_GAP: answerable question got refusal.
    """
    if not answer:
        return "ERR"
    refused = is_refuse(answer)
    if is_trap:
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    if expected_verdict == "REFUSED":
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    # expected ANSWERED
    return "REFUSE_GAP" if refused else "PASS_ANSWERED"


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(round((pct / 100.0) * (len(s) - 1)))
    return s[idx]


async def run_fixture(
    fixture_path: Path,
    *,
    pace_s: float,
    out_path: Path,
    label: str,
) -> dict[str, Any]:
    """Execute every turn from the fixture; write JSON to ``out_path``."""
    with open(fixture_path) as f:
        turns = json.load(f)
    print(f"Loaded {len(turns)} turns from {fixture_path}", flush=True)

    async with httpx.AsyncClient() as client:
        token = await get_self_token(client)
        print("Got self token (truncated):", token[:24], "...", flush=True)

        results: list[dict[str, Any]] = []
        for i, t in enumerate(turns, 1):
            connect_id = (
                f"loadtest-300q-{t.get('industry', '?')}-{int(time.time())}-{i}"
            )
            resp = await ask_one(
                client,
                token,
                bot_id=t["bot_id"],
                workspace_id=t["workspace_id"],
                channel_type=t.get("channel_type", "web"),
                question=t["question"],
                connect_id=connect_id,
            )
            answer = resp.get("answer", "")
            verdict = classify(
                answer,
                is_trap=bool(t.get("hallu_trap")),
                expected_verdict=t.get("expected_verdict", "ANSWERED"),
            )
            rec = {
                "id": t["id"],
                "industry": t.get("industry"),
                "bot_id": t["bot_id"],
                "pattern": t.get("pattern"),
                "hallu_trap": bool(t.get("hallu_trap")),
                "trap_kind": t.get("trap_kind"),
                "expected_verdict": t.get("expected_verdict"),
                "question": t["question"][:200],
                "answer": (answer or "")[:600],
                "answer_type": resp.get("answer_type"),
                "verdict": verdict,
                "top_score": resp.get("top_score"),
                "chunks_used": resp.get("chunks_used", 0),
                "latency_ms": resp.get("latency_ms"),
                "cost_usd": resp.get("cost_usd"),
                "trace_id": resp.get("trace_id"),
                "request_id": resp.get("request_id"),
                "error": resp.get("error"),
            }
            results.append(rec)
            short = (answer or "")[:50].replace("\n", " ")
            print(
                f"  [{i:03d}/{len(turns)}] {verdict:14s} "
                f"bot={t['bot_id']:22s} pat={t.get('pattern', '?'):18s} "
                f"trap={'Y' if t.get('hallu_trap') else 'N'} "
                f"lat={rec['latency_ms']:5d}ms | {short}",
                flush=True,
            )
            if pace_s > 0:
                await asyncio.sleep(pace_s)

    summary = _aggregate(results, label=label)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {"summary": summary, "results": results}, f,
            ensure_ascii=False, indent=2,
        )
    return summary


def _aggregate(results: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    """Build the top-level summary block from a list of per-turn records."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    trap_total = sum(1 for r in results if r["hallu_trap"])
    hallu_breach = sum(
        1 for r in results
        if r["hallu_trap"] and r["verdict"] == "HALLU_BREACH"
    )
    non_trap = sum(1 for r in results if not r["hallu_trap"])
    answered_pass = sum(
        1 for r in results
        if not r["hallu_trap"] and r["verdict"] == "PASS_ANSWERED"
    )
    refuse_gap = sum(
        1 for r in results
        if not r["hallu_trap"] and r["verdict"] == "REFUSE_GAP"
    )

    lats = [r["latency_ms"] for r in results if r.get("latency_ms")]
    costs = [
        r["cost_usd"] for r in results
        if isinstance(r.get("cost_usd"), (int, float))
    ]

    # Per-bot breakdown
    by_bot: dict[str, dict[str, int]] = {}
    for r in results:
        b = r["bot_id"]
        by_bot.setdefault(b, {"total": 0, "pass": 0, "hallu_breach": 0})
        by_bot[b]["total"] += 1
        if r["verdict"] in ("PASS_ANSWERED", "PASS_REFUSED"):
            by_bot[b]["pass"] += 1
        if r["verdict"] == "HALLU_BREACH":
            by_bot[b]["hallu_breach"] += 1

    return {
        "label": label,
        "total": len(results),
        "verdict_counts": counts,
        "hallu_trap_total": trap_total,
        "hallu_breach": hallu_breach,
        "hallu_zero_sacred": hallu_breach == 0,
        "non_trap_total": non_trap,
        "answered_pass": answered_pass,
        "refuse_gap": refuse_gap,
        "answered_pass_rate": (
            round(answered_pass / non_trap * 100, 1) if non_trap else 0.0
        ),
        "err": counts.get("ERR", 0),
        "p50_latency_ms": percentile(lats, 50),
        "p95_latency_ms": percentile(lats, 95),
        "avg_cost_usd": (sum(costs) / len(costs)) if costs else 0.0,
        "total_cost_usd": sum(costs),
        "by_bot": by_bot,
    }


async def main_async() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=DEFAULT_FIXTURE)
    ap.add_argument("--output", default=None)
    ap.add_argument(
        "--pace", type=float,
        default=DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
    )
    ap.add_argument(
        "--label", default=f"300q-{time.strftime('%Y%m%d_%H%M%S')}",
    )
    args = ap.parse_args()

    fixture_path = Path(args.questions)
    out_path = Path(
        args.output
        or f"reports/SMARTNESS_300Q_RESULT_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    summary = await run_fixture(
        fixture_path, pace_s=args.pace, out_path=out_path, label=args.label,
    )

    print()
    print("=" * 60)
    print("SMARTNESS 300Q SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print()
    print(f"Output: {out_path}")


if __name__ == "__main__":
    asyncio.run(main_async())
