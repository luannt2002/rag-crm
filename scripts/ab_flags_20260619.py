#!/usr/bin/env python3
"""Phase 4 A/B — multi-flag cost/latency sweep (Tests 2-5).

One shared A=baseline pass, then one treatment pass per flag (flipped via the
test-mode pipeline_config_overrides — NO DB mutation). Reports cost + latency
delta vs baseline per flag, plus an answer-similarity check so we catch any
quality regression (cost/latency wins must not change answers).

Flags swept (all default OFF except where noted):
  - adaptive_context_enabled      : prune weak chunks → fewer LLM ctx tokens (cost ↓)
  - speculative_retrieve_enabled  : parallel raw-embed retrieve → latency ↓ on hit
  - neighbor_expand_enabled       : ±1 sibling chunks → context ↑ (latency ↑, quality)
  - pipeline_multi_query_speculative_enabled : parallel MQ paraphrase (latency ↓ multi-hop)
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
OUT = REPO / "reports" / "validate_20260617" / "ab_flags_raw.jsonl"

CASES = [
    {"bot": "test-spa-id", "ws": "spa", "q": "Trị mụn giá bao nhiêu?"},
    {"bot": "test-spa-id", "ws": "spa", "q": "Có dịch vụ nào dưới 500k không?"},
    {"bot": "test-spa-id", "ws": "spa", "q": "Dịch vụ nào đắt nhất ạ?"},
    {"bot": "chinh-sach-xe", "ws": "xe", "q": "Chính sách bảo hành lốp xe như thế nào?"},
    {"bot": "thong-tu-09-2020-tt-nhnn", "ws": "legal", "q": "Thời hạn báo cáo sự cố là bao lâu?"},
    {"bot": "thong-tu-09-2020-tt-nhnn", "ws": "legal", "q": "Điều 56 quy định về việc gì?"},
]

TREATMENTS = {
    "adaptive_context": {"adaptive_context_enabled": True},
    "speculative_retrieve": {"speculative_retrieve_enabled": True},
    "neighbor_expand": {"neighbor_expand_enabled": True},
    "mq_speculative": {"pipeline_multi_query_speculative_enabled": True},
}


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=BYPASS, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("token") or d.get("access_token") or d["data"]["token"]


async def _ask(c, tok, case, arm, overrides, sem) -> dict:
    body = {
        "bot_id": case["bot"], "channel_type": "web", "workspace_id": case["ws"],
        "question": case["q"], "connect_id": f"abf-{arm}",
        "bypass_cache": True,
    }
    if overrides:
        body["pipeline_config_overrides"] = overrides
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **BYPASS}
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await c.post(
                f"{BASE}/api/ragbot/test/chat", headers=headers, json=body, timeout=120,
            )
            r.raise_for_status()
            d = r.json()
        except Exception as exc:  # noqa: BLE001 — driver: record + continue
            d = {"error": str(exc)}
        d["_wall_ms"] = round((time.perf_counter() - t0) * 1000)
        d["_q"] = case["q"]
        d["_arm"] = arm
        return d


def _cost(d: dict) -> float:
    return float(d.get("cost_usd") or 0.0)


def _ans(d: dict) -> str:
    return (d.get("answer") or "").strip()


async def main() -> int:
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        tasks = [_ask(c, tok, case, "baseline", None, sem) for case in CASES]
        for name, ov in TREATMENTS.items():
            tasks += [_ask(c, tok, case, name, ov, sem) for case in CASES]
        results = await asyncio.gather(*tasks)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for d in results:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    base = {d["_q"]: d for d in results if d["_arm"] == "baseline"}
    bc = sum(_cost(d) for d in base.values())
    bl = sorted(d["_wall_ms"] for d in base.values())
    print(f"=== Phase 4 multi-flag A/B — {len(CASES)} cases (raw → {OUT}) ===\n")
    print(f"[baseline] cost=${bc:.6f}  p50_wall={bl[len(bl)//2]}ms\n")
    print(f"{'flag':<22} {'cost_d%':>8} {'p50_d_ms':>9} {'ans_changed':>12}")
    for name in TREATMENTS:
        arm = {d["_q"]: d for d in results if d["_arm"] == name}
        tc = sum(_cost(d) for d in arm.values())
        tl = sorted(d["_wall_ms"] for d in arm.values())
        cost_d = (tc - bc) / bc * 100 if bc else 0.0
        p50_d = tl[len(tl) // 2] - bl[len(bl) // 2]
        # answer changed = how many answers differ materially from baseline
        changed = sum(
            1 for q in base
            if _ans(arm.get(q, {})) and _ans(base[q])
            and _ans(arm[q])[:120] != _ans(base[q])[:120]
        )
        print(f"{name:<22} {cost_d:>+7.1f}% {p50_d:>+8}ms {changed:>6}/{len(CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
