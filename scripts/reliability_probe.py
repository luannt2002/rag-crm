"""Honest reliability probe — measure real success / error / latency under load.

Motivation (all-flows audit 2026-07-10): the existing eval harnesses call the
chat endpoint without ``raise_for_status``, so an upstream ``503`` is recorded as
an empty content-miss and a load test reports "0 errors" when a large fraction of
requests actually failed. This probe classifies every response HONESTLY so the
real success / 503 / error / empty rate and latency are visible — the metric
foundation for any reliability change (e.g. lowering provider concurrency).

Domain-neutral: bot identity + questions come from a scenario JSON (same shape as
``tests/scenarios/*_deepdive60.json``: ``{bot_id, channel_type, workspace_id,
questions:[{q}]}``). Base URL + bypass token come from the environment. No bot,
brand, or provider literal in this file.

Usage:
    set -a && source .env && set +a
    python scripts/reliability_probe.py \
        --scenario tests/scenarios/<bot>_deepdive60.json \
        --concurrency 8 [--repeat 1] [--limit N]

Output: JSON summary + a human-readable table. Read-only against the app (POSTs
questions, no admin/DML). Bucketing is purely on HTTP status + answer presence —
it does NOT guess truncation (the provider masks that as ``finish_reason=stop``;
see the audit). Truncation is surfaced separately via the server-side
``llm_generation_finish`` log, not inferred here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import httpx

_BYPASS_HEADER = "X-Ragbot-Loadtest-Bypass"


def _bypass() -> dict[str, str]:
    return {_BYPASS_HEADER: os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}


async def _token(client: httpx.AsyncClient, base: str) -> str:
    r = await client.get(f"{base}/api/ragbot/test/tokens/self", headers=_bypass())
    r.raise_for_status()
    return r.json()["token"]


async def _ask(
    client: httpx.AsyncClient, base: str, token: str, bot: dict, q: str, cid: str
) -> dict:
    """One request. Returns an HONEST outcome dict (never raises)."""
    body = {
        "bot_id": bot["bot_id"],
        "channel_type": bot["channel_type"],
        "workspace_id": bot.get("workspace_id", ""),
        "question": q,
        "connect_id": cid,
        "bypass_cache": True,
        "debug": "full",
    }
    headers = {"Authorization": f"Bearer {token}", **_bypass()}
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{base}/api/ragbot/test/chat", json=body, headers=headers, timeout=180
        )
        dur_ms = int((time.perf_counter() - t0) * 1000)
        status = r.status_code
        if status == 503:
            return {"bucket": "upstream_503", "status": status, "dur_ms": dur_ms}
        if status >= 500:
            return {"bucket": "server_5xx", "status": status, "dur_ms": dur_ms}
        if status >= 400:
            return {"bucket": "client_4xx", "status": status, "dur_ms": dur_ms}
        answer = (r.json().get("answer") or "").strip()
        if not answer:
            return {"bucket": "empty_answer", "status": status, "dur_ms": dur_ms}
        return {
            "bucket": "answered",
            "status": status,
            "dur_ms": dur_ms,
            "ans_len": len(answer),
        }
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        dur_ms = int((time.perf_counter() - t0) * 1000)
        return {"bucket": "transport_error", "error": type(exc).__name__, "dur_ms": dur_ms}


async def main(scenario: str, concurrency: int, repeat: int, limit: int | None) -> None:
    base = os.environ.get("RAGBOT_BASE_URL", "http://localhost:3004")
    sc = json.load(open(scenario))
    bot = {
        "bot_id": sc["bot_id"],
        "channel_type": sc["channel_type"],
        "workspace_id": sc.get("workspace_id", ""),
    }
    questions = [q["q"] for q in sc["questions"]]
    if limit:
        questions = questions[:limit]
    tasks_spec = [
        (q, f"probe-{i}-r{it}")
        for it in range(repeat)
        for i, q in enumerate(questions)
    ]

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        token = await _token(client, base)

        async def _bounded(q: str, cid: str) -> dict:
            async with sem:
                return await _ask(client, base, token, bot, q, cid)

        results = await asyncio.gather(*[_bounded(q, cid) for q, cid in tasks_spec])

    n = len(results)
    buckets: dict[str, int] = {}
    for r in results:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    durs = sorted(r["dur_ms"] for r in results if "dur_ms" in r)

    def _pct(p: float) -> int:
        return durs[min(len(durs) - 1, int(len(durs) * p))] if durs else 0

    answered = buckets.get("answered", 0)
    errors = n - answered - buckets.get("empty_answer", 0)
    summary = {
        "scenario": scenario,
        "concurrency": concurrency,
        "n": n,
        "answered": answered,
        "answered_pct": round(answered / n * 100, 1) if n else 0,
        "error_rate_pct": round(errors / n * 100, 1) if n else 0,
        "buckets": buckets,
        "latency_ms": {
            "p50": _pct(0.50),
            "p95": _pct(0.95),
            "max": durs[-1] if durs else 0,
        },
    }
    print("=" * 60)
    print(f"RELIABILITY PROBE — concurrency={concurrency} n={n}")
    print("=" * 60)
    for b, c in sorted(buckets.items(), key=lambda x: -x[1]):
        print(f"  {b:18} {c:4}  ({c/n*100:4.1f}%)")
    print(f"  {'-'*30}")
    print(f"  answered           {answered:4}  ({summary['answered_pct']}%)")
    print(f"  error_rate (non-answer/non-empty) {summary['error_rate_pct']}%")
    print(f"  latency ms  p50={summary['latency_ms']['p50']}"
          f"  p95={summary['latency_ms']['p95']}  max={summary['latency_ms']['max']}")
    print()
    print(json.dumps(summary))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    asyncio.run(main(args.scenario, args.concurrency, args.repeat, args.limit))
