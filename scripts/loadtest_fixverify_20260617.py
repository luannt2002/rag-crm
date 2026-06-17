#!/usr/bin/env python3
"""Focused load test verifying this session's fixes + core flows per bot.

Targets: (a) canonical number standard (dưới 500k / dưới 700.000),
(b) superlative aggregation (đắt nhất / rẻ nhất), (c) factoid coverage,
(d) HALLU traps (out-of-corpus → must refuse, never fabricate).

Parallel (asyncio.gather + semaphore N=8 per CLAUDE.md ragas_parallel rule).
Logs full input/output JSON to reports/validate_20260617/fixverify_raw.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}
OUT = REPO_ROOT / "reports" / "validate_20260617" / "fixverify_raw.jsonl"

# kind: factoid | superlative | range | trap
# trap = out-of-corpus → expected behaviour is a refusal (HALLU guard).
CASES: list[dict] = [
    # ---- spa (workspace spa) ----
    {"bot": "test-spa-id", "ws": "spa", "kind": "superlative", "q": "Dịch vụ nào đắt nhất ạ?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "superlative", "q": "Dịch vụ nào rẻ nhất bên mình?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "range", "q": "Có dịch vụ nào dưới 500k không?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "range", "q": "Liệt kê dịch vụ dưới 700.000 đồng"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "factoid", "q": "Trị mụn giá bao nhiêu?"},
    {"bot": "test-spa-id", "ws": "spa", "kind": "trap", "q": "Phun xăm thẩm mỹ chân mày giá bao nhiêu?"},
    # ---- xe (workspace xe) ----
    {"bot": "chinh-sach-xe", "ws": "xe", "kind": "factoid", "q": "Lốp 195/65R15 khi nào về hàng?"},
    {"bot": "chinh-sach-xe", "ws": "xe", "kind": "factoid", "q": "Chính sách bảo hành lốp xe như thế nào?"},
    {"bot": "chinh-sach-xe", "ws": "xe", "kind": "trap", "q": "Giá lốp Michelin Pilot Sport bao nhiêu?"},
    # ---- legal (workspace legal) ----
    {"bot": "thong-tu-09-2020-tt-nhnn", "ws": "legal", "kind": "factoid", "q": "Điều 56 quy định về việc gì?"},
    {"bot": "thong-tu-09-2020-tt-nhnn", "ws": "legal", "kind": "factoid", "q": "Thời hạn báo cáo sự cố là bao lâu?"},
    {"bot": "thong-tu-09-2020-tt-nhnn", "ws": "legal", "kind": "trap", "q": "Mức phạt vi phạm hành chính tối đa là bao nhiêu tiền?"},
]


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("token") or d.get("access_token") or d["data"]["token"]


async def _ask(c: httpx.AsyncClient, tok: str, case: dict, sem: asyncio.Semaphore) -> dict:
    body = {
        "bot_id": case["bot"], "channel_type": "web", "workspace_id": case["ws"],
        "question": case["q"], "connect_id": f"fixverify-{case['kind']}",
        "bypass_cache": True,
    }
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **_BYPASS}
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await c.post(f"{BASE}/api/ragbot/test/chat", headers=headers, json=body, timeout=120)
            r.raise_for_status()
            d = r.json()
        except Exception as exc:  # noqa: BLE001 — loadtest driver: record + continue
            d = {"error": str(exc)}
        d["_latency_ms"] = round((time.perf_counter() - t0) * 1000)
        d["_case"] = case
        return d


def _answer_of(d: dict) -> str:
    for k in ("answer", "response", "message", "content"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    data = d.get("data") or {}
    return data.get("answer") or data.get("response") or ""


async def main() -> int:
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        results = await asyncio.gather(*[_ask(c, tok, case, sem) for case in CASES])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for d in results:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"=== {len(results)} answers (raw → {OUT}) ===\n")
    for d in results:
        case = d["_case"]
        ans = _answer_of(d)
        err = d.get("error", "")
        print(f"[{case['bot']:>24} | {case['kind']:>10}] {case['q']}")
        print(f"   → {('ERROR: ' + err) if err else ans[:400]}")
        print(f"   (latency {d.get('_latency_ms')}ms, chunks_used={d.get('chunks_used', d.get('data', {}).get('chunks_used', '?'))})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
