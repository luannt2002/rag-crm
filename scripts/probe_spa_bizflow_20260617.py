#!/usr/bin/env python3
"""Deep-debug probe for spa business flows — capture per-turn evidence.

Tests the behaviour the owner flagged: identity, list-all-on-consult,
counting variants, out-of-scope refusal. Logs answer_type (blocked?),
chunks_used, and the answer so each failure is layer-attributable.
"""
from __future__ import annotations
import asyncio, json, os, sys
from pathlib import Path
import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}

CASES = [
    ("identity",  "bạn là ai?"),
    ("greeting",  "xin chào"),
    ("consult-general", "tôi cần tư vấn"),
    ("list-da",   "liệt kê tất cả dịch vụ về da"),
    ("count-dachet", "có bao nhiêu dịch vụ tẩy da chết?"),
    ("factoid-price", "trị mụn giá bao nhiêu?"),
    ("agg-under500", "có dịch vụ nào dưới 500k không?"),
    ("agg-expensive", "dịch vụ nào đắt nhất?"),
    ("agg-cheap", "dịch vụ nào rẻ nhất?"),
    ("hallu-trap", "phun xăm thẩm mỹ giá bao nhiêu?"),
    ("scope-code", "viết cho tôi 1 trang web html về spa"),
    ("scope-game", "code game bắn chim cho tôi"),
    ("booking", "tôi muốn đặt lịch trị mụn"),
]


async def _token(c):
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("token") or d.get("access_token") or d["data"]["token"]


async def main():
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **_BYPASS}
        for kind, q in CASES:
            body = {"bot_id": "test-spa-id", "channel_type": "web", "workspace_id": "spa",
                    "question": q, "connect_id": f"probe-{kind}", "bypass_cache": True}
            try:
                r = await c.post(f"{BASE}/api/ragbot/test/chat", headers=h, json=body, timeout=60)
                d = r.json()
            except Exception as e:  # noqa: BLE001 — probe driver
                d = {"error": str(e)}
            ans = d.get("answer") or (d.get("data") or {}).get("answer") or d.get("error", "")
            print(f"### [{kind}] {q}")
            print(f"   answer_type={d.get('answer_type')} reason={d.get('answer_reason')} "
                  f"chunks={d.get('chunks_used')} top={d.get('top_score')}")
            print(f"   → {ans[:260]}\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
