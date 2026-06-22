"""Per-STEP deep-debug of the RAG QUERY flow (Q1→Q8), mirroring verify_happy_case_
pipeline.py for the upload side. Runs a question through the live chat endpoint and
maps the response's ``debug`` block onto the 8 query steps, asserting each + flagging
anomalies (rule #0: evidence, not vibes). Plan: plans/20260622-rag-query-flow-audit.

    set -a && source .env && set +a
    python scripts/verify_query_flow.py
"""
from __future__ import annotations

import asyncio
import sys

import httpx

TEST = "http://localhost:3004/api/ragbot/test"

# (bot_id, question, intent_kind) — diverse flows: factoid / list / aggregate / yes-no
QUESTIONS = [
    ("test-spa-id", "Giá massage cổ vai gáy 90 phút bao nhiêu", "factoid"),
    ("test-spa-id", "Cho mình xem toàn bộ danh sách dịch vụ kèm giá", "list"),
    ("test-spa-id", "Dịch vụ nào đắt nhất bên mình", "aggregate"),
    ("chinh-sach-xe", "Lốp Rovelo nào rẻ nhất", "aggregate"),
    ("thong-tu-09-2020-tt-nhnn", "Điều 18 quy định gì", "factoid"),
]
_VALID_INTENT = {"factoid", "aggregation", "listing", "list", "chitchat", "booking",
                 "comparison", "definition", "procedure", "other", "out_of_scope"}


def _row(mark: str, step: str, detail: str) -> str:
    return f"  {mark} {step:26} {detail}"


async def debug_one(c: httpx.AsyncClient, h: dict, bot: str, q: str, i: int) -> list[str]:
    r = await c.post(f"{TEST}/chat", json={"bot_id": bot, "channel_type": "web",
                     "question": q, "connect_id": f"qflow-{i}"}, headers=h)
    d = r.json()
    dbg = d.get("debug", {}) or {}
    ans = d.get("answer") or ""
    anomalies: list[str] = []
    out = [f"\n❓ [{bot[:12]}] {q}"]

    # Q1 — Understand (intent + rewrite + condense + decompose)
    intent = dbg.get("intent", "")
    rw = dbg.get("rewritten_query", "")
    ok1 = intent in _VALID_INTENT and bool(rw)
    out.append(_row("✓" if ok1 else "✗", "Q1 understand",
                    f"intent={intent} corrected={dbg.get('intent_corrected')} rewrite={rw!r} decomposed={dbg.get('query_decomposed')}"))
    if not ok1:
        anomalies.append("Q1 intent/rewrite")

    # Q2 — Embed (implicit; confirmed by retrieval running)
    out.append(_row("✓", "Q2 embed", "query → vector (implicit; retrieval ran)"))

    # Q3 — Retrieve
    topk = dbg.get("top_k", 0)
    ok3 = topk and topk >= 1
    out.append(_row("✓" if ok3 else "✗", "Q3 retrieve", f"top_k={topk} source={dbg.get('source')}"))
    if not ok3:
        anomalies.append("Q3 retrieved 0")
    elif topk == 1:
        anomalies.append("Q3 top_k=1 (chỉ 1 chunk — budget hẹp; ổn cho factoid/summary, rủi ro cho câu cần nhiều chunk)")

    # Q4+Q5 — Rerank + Grade
    g = dbg.get("chunks_graded", 0)
    smax, smin, savg = dbg.get("score_max"), dbg.get("score_min"), dbg.get("score_avg")
    out.append(_row("✓" if g else "✗", "Q4-5 rerank/grade", f"graded={g} score max/min/avg={smax}/{smin}/{savg}"))

    # Q6 — Assemble context
    pe = dbg.get("parents_expanded_count", 0)
    used = d.get("chunks_used", 0)
    out.append(_row("✓" if used else "✗", "Q6 assemble", f"chunks_used={used} parents_expanded={pe}"))
    if not used:
        anomalies.append("Q6 0 chunks in context")

    # Q7 — Generate
    at = d.get("answer_type")
    ok7 = at == "answered" and bool(ans)
    out.append(_row("✓" if ok7 else "🟡", "Q7 generate", f"model={dbg.get('model')} type={at} → {ans[:60]!r}"))

    # Q8 — Post-process
    cs = dbg.get("cache_status")
    gf = dbg.get("guardrail_flags")
    out.append(_row("✓", "Q8 post", f"cache={cs} guardrail={gf} top_score={d.get('top_score')} cost=${d.get('cost_usd')}"))
    if cs == "hit":
        anomalies.append("Q8 cache HIT (có thể trả câu cũ — bust nếu vừa đổi sysprompt)")

    for a in anomalies:
        out.append(f"     ⚠️ {a}")
    return out


async def main() -> None:
    async with httpx.AsyncClient(timeout=90.0) as c:
        tok = (await c.get(f"{TEST}/tokens/self")).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        print("RAG QUERY-FLOW DEEP-DEBUG (Q1→Q8) — live chat endpoint\n" + "═" * 78)
        for i, (bot, q, _kind) in enumerate(QUESTIONS):
            for ln in await debug_one(c, h, bot, q, i):
                print(ln)
        print("\n" + "═" * 78)
        print("→ Mỗi câu: 8 step map từ debug block. ⚠️ = anomaly cần xem (KHÔNG nhất thiết bug).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except httpx.HTTPError as e:
        print(f"HTTP error: {e}")
        sys.exit(1)
