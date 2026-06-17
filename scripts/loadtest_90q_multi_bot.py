"""Load test runner cho 90Q multi-bot fixture.

Reads ``reports/CODER_LOADTEST_90Q_FIXTURE.json`` (list of 90 turns, each
with own ``bot_id``/``workspace_id``/``hallu_trap``) and replays them
against the running ragbot at ``$RAGBOT_BASE_URL`` (default localhost:3004).

Produces a JSON result + a Markdown summary (pass rate, HALLU breach, p95,
cost/turn) under ``reports/``.
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
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")


def _bypass_headers() -> dict[str, str]:
    token = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    if not token:
        return {}
    return {RAGBOT_LOADTEST_BYPASS_HEADER: token}


async def get_self_token(client: httpx.AsyncClient) -> str:
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
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        **_bypass_headers(),
    }
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/api/ragbot/test/chat",
            headers=headers,
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
        d["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        return d
    except Exception as exc:
        return {
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }


def classify(
    answer: str,
    is_trap: bool,
    expected_verdict: str,
) -> str:
    """Return one of: PASS_ANSWERED, PASS_REFUSED, HALLU_BREACH, REFUSE_GAP, ERR."""
    if not answer:
        return "ERR"
    refused = is_refuse(answer)
    if is_trap:
        # Sacred: trap MUST refuse.
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    # Non-trap with expected_verdict
    if expected_verdict == "ANSWERED":
        return "REFUSE_GAP" if refused else "PASS_ANSWERED"
    if expected_verdict == "REFUSED":
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    return "PASS_ANSWERED" if not refused else "REFUSE_GAP"


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(round((pct / 100.0) * (len(s) - 1)))
    return s[idx]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--questions",
        default="reports/CODER_LOADTEST_90Q_FIXTURE.json",
    )
    ap.add_argument("--output", default=None)
    ap.add_argument("--pace", type=float, default=0.1)
    ap.add_argument(
        "--label", default=f"90q-{time.strftime('%Y%m%d_%H%M%S')}",
    )
    args = ap.parse_args()

    fixture_path = Path(args.questions)
    with open(fixture_path) as f:
        turns = json.load(f)
    print(f"Loaded {len(turns)} turns from {fixture_path}", flush=True)

    out_path = Path(
        args.output
        or f"reports/LOADTEST_90Q_RESULT_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )

    async with httpx.AsyncClient() as client:
        token = await get_self_token(client)
        print("Got self token (truncated):", token[:24], "...", flush=True)

        results: list[dict[str, Any]] = []
        for i, t in enumerate(turns, 1):
            connect_id = f"loadtest-90q-{t.get('persona', '?')}-{int(time.time())}"
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
                "persona": t.get("persona"),
                "intent_expected": t.get("intent_expected"),
                "bot_id": t["bot_id"],
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
            short = (answer or "")[:60].replace("\n", " ")
            print(
                f"  [{i:02d}/{len(turns)}] {verdict:14s} "
                f"bot={t['bot_id']:14s} trap={'Y' if t.get('hallu_trap') else 'N'} "
                f"score={rec['top_score']!s:6s} "
                f"lat={rec['latency_ms']:5d}ms "
                f"| {short}",
                flush=True,
            )
            if args.pace:
                await asyncio.sleep(args.pace)

    # Aggregate
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    trap_total = sum(1 for r in results if r["hallu_trap"])
    hallu_breach = sum(
        1 for r in results
        if r["hallu_trap"] and r["verdict"] == "HALLU_BREACH"
    )
    non_trap = sum(1 for r in results if not r["hallu_trap"])
    refuse_gap = sum(
        1 for r in results
        if not r["hallu_trap"] and r["verdict"] == "REFUSE_GAP"
    )
    answered_pass = sum(
        1 for r in results
        if not r["hallu_trap"] and r["verdict"] == "PASS_ANSWERED"
    )
    err = counts.get("ERR", 0)

    lats = [r["latency_ms"] for r in results if r.get("latency_ms")]
    costs = [
        r["cost_usd"] for r in results
        if isinstance(r.get("cost_usd"), (int, float))
    ]

    summary = {
        "label": args.label,
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
        "err": err,
        "p50_latency_ms": percentile(lats, 50),
        "p95_latency_ms": percentile(lats, 95),
        "avg_cost_usd": (sum(costs) / len(costs)) if costs else 0.0,
        "total_cost_usd": sum(costs),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {"summary": summary, "results": results}, f,
            ensure_ascii=False, indent=2,
        )

    print()
    print("=" * 60)
    print("LOAD TEST 90Q SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print()
    print(f"Output: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
