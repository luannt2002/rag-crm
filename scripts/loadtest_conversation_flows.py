"""Multi-turn conversation load-test for the 3 demo bots.

Runs realistic human-like conversation flows (NOT single factoids) against
spa / xe / legal, keeping one ``connect_id`` per flow so the bot has history
(coreference, booking-slot memory). Each flow's turns run SEQUENTIALLY (history
dependency); flows run in PARALLEL (asyncio.gather + semaphore, per CLAUDE.md
ragas-parallel rule).

Scoring is agent/operator-side (no LLM judge): per turn we record
answer_type + answer + intent, and flag the checks that matter
(coreference drift, refuse-on-OOS-trap=HALLU-safe, multi-variant listing).

Run:  set -a && source .env && set +a && python scripts/loadtest_conversation_flows.py
Source of truth for the flows: tests/scenarios/conversation_flows_3bots.md
"""
from __future__ import annotations

import asyncio
import os

import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}
_CONCURRENCY = int(os.getenv("LOADTEST_CONCURRENCY", "6"))

# Each flow: (flow_id, bot_id, workspace_id, [(turn_text, check_hint), ...])
# check_hint: 'oos' = must refuse (HALLU trap); 'variant' = must list ≥2;
#             'coref' = must stay on prior subject; '' = normal answer.
FLOWS = [
    ("spa-A-booking", "test-spa-id", "spa", [
        ("chào em", ""),
        ("bên em có dịch vụ gì cho da không ạ", ""),
        ("da mình dạo này hay nổi mụn", "coref"),
        ("giá nhiêu vậy em", "coref"),
        ("ok cho chị đặt lịch thử", ""),
        ("Hương, 0901234567", ""),
        ("chiều mai 3h nhé", ""),
    ]),
    ("spa-B-variant", "test-spa-id", "spa", [
        ("tẩy da chết giá bao nhiêu", "variant"),
        ("cái ủ trắng đó làm bao lâu", "coref"),
    ]),
    ("spa-C-process", "test-spa-id", "spa", [
        ("massage cổ vai gáy thế nào", ""),
        ("quy trình gồm những gì", "coref"),
    ]),
    ("spa-D-oos", "test-spa-id", "spa", [
        ("spa mình có bán mỹ phẩm mang về nhà không", "oos"),
        ("có dịch vụ phun xăm thẩm mỹ chứ", "oos"),
    ]),
    ("xe-A-variant", "chinh-sach-xe", "xe", [
        ("cho hỏi lốp 265/50R20 giá nhiêu", "variant"),
        ("loại nào còn nhiều hàng hơn", "coref"),
    ]),
    ("xe-C-oos", "chinh-sach-xe", "xe", [
        ("xe sedan thì nên dùng lốp loại nào", "oos"),
        ("lốp 999/99R99 giá nhiêu", "oos"),
    ]),
    ("legal-A-coref", "thong-tu-09-2020-tt-nhnn", "legal", [
        ("Điều 4 quy định về cái gì", ""),
        ("khoản 2 của điều đó nói gì", "coref"),
    ]),
    ("legal-C-oos", "thong-tu-09-2020-tt-nhnn", "legal", [
        ("luật doanh nghiệp 2020 quy định vốn điều lệ thế nào", "oos"),
        ("Điều 999 nói gì", "oos"),
    ]),
]


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


async def _ask(c, tok, bot, ws, q, cid):
    body = {"bot_id": bot, "channel_type": "web", "workspace_id": ws,
            "question": q, "connect_id": cid, "bypass_cache": True}
    try:
        r = await c.post(f"{BASE}/api/ragbot/test/chat",
                         headers={"Authorization": f"Bearer {tok}", **_BYPASS},
                         json=body, timeout=120)
        return r.json()
    except Exception as exc:  # noqa: BLE001
        return {"answer": f"ERR {exc}", "answer_type": "error"}


_REFUSE_MARKERS = ("chưa có thông tin", "không tìm thấy", "chưa tìm thấy",
                   "liên hệ", "không có thông tin", "đang được chuẩn bị")


async def _run_flow(c, tok, sem, flow):
    flow_id, bot, ws, turns = flow
    cid = f"loadtest-{flow_id}"
    out = []
    async with sem:
        for q, hint in turns:
            d = await _ask(c, tok, bot, ws, q, cid)
            ans = (d.get("answer") or "").replace("\n", " ")
            atype = d.get("answer_type")
            refused = any(m in ans.lower() for m in _REFUSE_MARKERS) or atype in ("blocked", "refused")
            flag = "ok"
            if hint == "oos":
                flag = "PASS(refused)" if refused else "⚠️CHECK-HALLU(answered an OOS trap)"
            elif hint == "variant":
                flag = "PASS(listed)" if ("\n" in (d.get("answer") or "") or "các loại" in ans or ans.count("đồng") + ans.count("đ/") >= 2) else "⚠️CHECK(only 1 variant?)"
            elif hint == "coref":
                flag = "answered(verify-subject)"
            out.append((q, atype, flag, ans[:130]))
    return flow_id, out


async def main():
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        sem = asyncio.Semaphore(_CONCURRENCY)
        results = await asyncio.gather(*[_run_flow(c, tok, sem, f) for f in FLOWS])
    print("=" * 78)
    print("MULTI-TURN CONVERSATION LOAD TEST — 3 bots")
    print("=" * 78)
    traps = passed = 0
    for flow_id, turns in results:
        print(f"\n### {flow_id}")
        for q, atype, flag, ans in turns:
            print(f"  [{atype}] «{q}»")
            print(f"      → {ans}")
            print(f"      check: {flag}")
            if "PASS(refused)" in flag:
                traps += 1; passed += 1
            elif "CHECK-HALLU" in flag:
                traps += 1
    print("\n" + "=" * 78)
    print(f"OOS traps refused (HALLU-safe): {passed}/{traps}")
    print("Coreference + variant turns: eyeball the transcript above for correct subject.")


if __name__ == "__main__":
    asyncio.run(main())
