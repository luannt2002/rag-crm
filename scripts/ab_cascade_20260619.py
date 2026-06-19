#!/usr/bin/env python3
"""Phase 4 A/B — cascade_routing_enabled (cost/latency, quality unchanged).

Runs each question twice against the live test-chat endpoint: A=baseline
(cascade OFF) vs B=treatment (cascade ON via per-request pipeline_config
override — NO DB mutation). Cascade routes SIMPLE intents to the cheap LLM
tier (nano) → expect cost ↓ on factoid/chitchat with answer unchanged.

Measures cost_usd + tokens + duration_ms per arm, aggregates the delta.
bypass_cache=True so both arms exercise the full pipeline (no cache skew).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}
OUT = REPO / "reports" / "validate_20260617" / "ab_cascade_raw.jsonl"

# Mostly SIMPLE intents (where cascade→nano helps) + 1 complex control.
CASES = [
    {"bot": "test-spa-id", "ws": "spa", "kind": "factoid", "q": "Trị mụn giá bao nhiêu?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "factoid", "q": "Gội đầu thư giãn giá bao nhiêu?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "chitchat", "q": "Shop ơi cho hỏi xíu được không ạ?"},
    {"bot": "chinh-sach-xe", "ws": "xe", "kind": "factoid", "q": "Chính sách bảo hành lốp xe như thế nào?"},
    {"bot": "thong-tu-09-2020-tt-nhnn", "ws": "legal", "kind": "factoid", "q": "Thời hạn báo cáo sự cố là bao lâu?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "superlative", "q": "Dịch vụ nào đắt nhất ạ?"},
]


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=BYPASS, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("token") or d.get("access_token") or d["data"]["token"]


async def _ask(c, tok, case, arm, overrides, sem) -> dict:
    body = {
        "bot_id": case["bot"], "channel_type": "web", "workspace_id": case["ws"],
        "question": case["q"], "connect_id": f"ab-{arm}-{case['kind']}",
        "bypass_cache": True,
    }
    if overrides:
        body["pipeline_config_overrides"] = overrides
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **BYPASS}
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await c.post(f"{BASE}/api/ragbot/test/chat", headers=headers, json=body, timeout=120)
            r.raise_for_status()
            d = r.json()
        except Exception as exc:
            d = {"error": str(exc)}
        d["_wall_ms"] = round((time.perf_counter() - t0) * 1000)
        d["_case"] = case
        d["_arm"] = arm
        return d


async def main() -> int:
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        tasks = []
        for case in CASES:
            tasks.append(_ask(c, tok, case, "A_baseline", None, sem))
            tasks.append(_ask(c, tok, case, "B_cascade", {"cascade_routing_enabled": True}, sem))
        results = await asyncio.gather(*tasks)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for d in results:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def _cost(d: dict) -> float:
        return float(d.get("cost_usd") or 0.0)

    def _toks(d: dict) -> int:
        t = d.get("tokens") or {}
        return int(t.get("prompt", 0) or 0) + int(t.get("completion", 0) or 0)

    by = {"A_baseline": [], "B_cascade": []}
    for d in results:
        by[d["_arm"]].append(d)
    print(f"=== A/B cascade_routing — {len(CASES)} cases x 2 arms (raw → {OUT}) ===\n")
    print(f"{'case':<42} {'arm':<11} {'cost_usd':>10} {'tokens':>7} {'wall_ms':>8}  answer[:40]")
    for case in CASES:
        for arm in ("A_baseline", "B_cascade"):
            d = next(x for x in by[arm] if x["_case"]["q"] == case["q"])
            ans = (d.get("answer") or d.get("error") or "")[:40].replace("\n", " ")
            print(f"{case['q'][:42]:<42} {arm:<11} {_cost(d):>10.6f} {_toks(d):>7} {d.get('_wall_ms'):>8}  {ans}")
        print()
    for arm in ("A_baseline", "B_cascade"):
        rows = by[arm]
        tc = sum(_cost(d) for d in rows)
        tt = sum(_toks(d) for d in rows)
        tl = sorted(d["_wall_ms"] for d in rows)
        p50 = tl[len(tl) // 2]
        print(f"[{arm}] total_cost=${tc:.6f}  total_tokens={tt}  p50_wall={p50}ms  n={len(rows)}")
    a, b = by["A_baseline"], by["B_cascade"]
    ca, cb = sum(_cost(d) for d in a), sum(_cost(d) for d in b)
    if ca > 0:
        print(f"\n>>> cost delta B vs A: {(cb - ca) / ca * 100:+.1f}%  (A=${ca:.6f} → B=${cb:.6f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
