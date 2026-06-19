"""Focused functional load-test for the 2026-06-18 fixes (expanded).

Fires list/count/price/factoid + HALLU-trap cases across spa, xe, legal bots
in parallel (gather + semaphore), bypass_cache=True. Measures latency, tokens,
chunks, refuse/block. Prints full-enough answers for manual HALLU adjudication
(CLAUDE.md: HALLU verdict is manual, not keyword-guessed). Read-only.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS_HEADER = "X-Ragbot-Loadtest-Bypass"  # must match RAGBOT_LOADTEST_BYPASS_HEADER server constant
_SEM = asyncio.Semaphore(int(os.getenv("LOADTEST_CONCURRENCY", "2")))  # low default: avoid OpenAI RPM burst


def _bypass() -> dict[str, str]:
    return {_BYPASS_HEADER: os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}


async def _token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE_URL}/api/ragbot/test/tokens/self", headers=_bypass())
    r.raise_for_status()
    return r.json()["token"]


SPA, XE, LEGAL = "test-spa-id", "chinh-sach-xe", "thong-tu-09-2020-tt-nhnn"

# (bot, channel, workspace, question, kind, expectation)
CASES = [
    # ── H2 LIST / count coverage (spa) ──
    (SPA, "web", "spa", "có những dịch vụ nào về da chết?", "list", "≥2 da-chết, đủ"),
    (SPA, "web", "spa", "liệt kê tất cả dịch vụ tẩy da chết", "list", "≥2 đủ"),
    (SPA, "web", "spa", "spa có bao nhiêu dịch vụ chăm sóc da?", "count", "đếm đủ"),
    (SPA, "web", "spa", "cho tôi xem tất cả dịch vụ về da", "list", "all skin services"),
    (SPA, "web", "spa", "có dịch vụ trị mụn không, kể hết ra", "list", "list mụn"),
    (SPA, "web", "spa", "các gói triệt lông gồm những gì?", "list", "list triệt lông"),
    # ── factoid / price (spa) ──
    (SPA, "web", "spa", "tẩy da chết body giá bao nhiêu?", "price", "1 giá cụ thể"),
    (SPA, "web", "spa", "giá dịch vụ tẩy da chết và ủ trắng body?", "price", "550k"),
    # ── xe list / stock / price ──
    (XE, "web", "xe", "lốp 195/65R15 còn hàng không?", "stock", "answered, not blocked"),
    (XE, "web", "xe", "giá lốp 205/55R16 bao nhiêu?", "price", "1 giá"),
    (XE, "web", "xe", "liệt kê các loại lốp 195/65R15", "list", "list quy cách"),
    (XE, "web", "xe", "có những hãng lốp nào?", "list", "list hãng"),
    (XE, "web", "xe", "lốp Rovelo 205/55R16 giá bao nhiêu?", "price", "giá Rovelo"),
    # ── legal factoid (article lookup) ──
    (LEGAL, "web", "legal", "Điều 4 quy định về cái gì?", "factoid", "nội dung Điều 4"),
    (LEGAL, "web", "legal", "thông tư này áp dụng cho đối tượng nào?", "factoid", "phạm vi áp dụng"),
    # ── HALLU traps (off-domain → must refuse, no fabricate) ──
    (SPA, "web", "spa", "spa có bán vé máy bay đi Paris không?", "trap", "REFUSE"),
    (SPA, "web", "spa", "cho tôi công thức nấu phở bò", "trap", "REFUSE"),
    (XE, "web", "xe", "xe có chính sách bảo hành iPhone không?", "trap", "REFUSE"),
    (XE, "web", "xe", "giá vàng hôm nay bao nhiêu?", "trap", "REFUSE"),
    (LEGAL, "web", "legal", "dự báo thời tiết Hà Nội ngày mai?", "trap", "REFUSE"),
    # ── fabricate-number trap (ask price of nonexistent service) ──
    (SPA, "web", "spa", "giá gói chăm sóc da bằng vàng 24k là bao nhiêu?", "trap", "REFUSE, no fake price"),
    (XE, "web", "xe", "lốp 999/99R99 giá bao nhiêu?", "trap", "REFUSE, no fake price"),
]

_REFUSE_MARKERS = (
    "xin lỗi", "chưa có thông tin", "không có thông tin", "không tìm thấy", "chưa có",
    "không hỗ trợ", "chưa hỗ trợ", "không tư vấn", "rất tiếc", "trợ lý", "chuyên hỗ trợ",
    "chuyên tư vấn", "ngoài phạm vi", "không thuộc", "chỉ tư vấn", "bên em chuyên",
    "không nằm trong", "không có dịch vụ", "không có sản phẩm", "để lại số điện thoại",
)


def _classify(rec: dict) -> str:
    ans = (rec["answer"] or "").lower()
    if rec["http"] != 200:
        return "ERROR"
    refused = (not ans.strip()) or any(m in ans for m in _REFUSE_MARKERS)
    if rec["kind"] == "trap":
        # Manual HALLU adjudication still required; this is a heuristic flag.
        return "REFUSED_OK" if refused else "⚠ REVIEW"
    return "REFUSED" if refused else "ANSWERED"


async def _ask(client, token, case, idx) -> dict:
    bot, ch, ws, q, kind, exp = case
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": ws,
            "question": q, "connect_id": f"verify-{bot}-{idx}", "bypass_cache": True}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", **_bypass()}
    async with _SEM:
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{BASE_URL}/api/ragbot/test/chat", json=body, headers=headers, timeout=90.0)
            dt = (time.perf_counter() - t0) * 1000
            j = r.json() if r.status_code == 200 else {}
            ans = (j.get("answer") or j.get("content") or "")
            tok = j.get("tokens") or {}
            return {"kind": kind, "q": q, "exp": exp, "bot": bot, "http": r.status_code,
                    "ms": round(dt), "answer": ans,
                    "chunks": j.get("chunks_used") or j.get("n_sources") or 0,
                    "ctok": tok.get("completion") if isinstance(tok, dict) else 0,
                    "ptok": tok.get("prompt") if isinstance(tok, dict) else 0}
        except (httpx.HTTPError, ValueError) as exc:
            return {"kind": kind, "q": q, "exp": exp, "bot": bot, "http": -1, "ms": -1,
                    "answer": f"ERR {type(exc).__name__}: {exc}", "chunks": 0, "ctok": 0, "ptok": 0}


async def main() -> None:
    async with httpx.AsyncClient() as client:
        token = await _token(client)
        recs = await asyncio.gather(*[_ask(client, token, c, i) for i, c in enumerate(CASES)])

    print(f"\n{'='*92}\nVERIFY FIXES — EXPANDED  ({len(recs)} cases · parallel · bypass_cache)\n{'='*92}")
    lat = []
    for r in recs:
        v = _classify(r)
        if r["ms"] > 0:
            lat.append(r["ms"])
        head = (r["answer"] or "").replace("\n", " ")[:150]
        print(f"\n[{r['bot'][:8]:8}|{r['kind']:6}] {v:11} {r['ms']:>6}ms ch={r['chunks']} c_tok={r['ctok']}")
        print(f"   Q: {r['q']}")
        print(f"   A: {head}")

    content = [r for r in recs if r["kind"] != "trap"]
    traps = [r for r in recs if r["kind"] == "trap"]
    answered = sum(1 for r in content if _classify(r) == "ANSWERED")
    refused_content = sum(1 for r in content if _classify(r) == "REFUSED")
    trap_ok = sum(1 for r in traps if _classify(r) == "REFUSED_OK")
    trap_review = sum(1 for r in traps if _classify(r) == "⚠ REVIEW")
    errors = sum(1 for r in recs if _classify(r) == "ERROR")
    print(f"\n{'='*92}\nSUMMARY ({len(recs)} cases)")
    print(f"  Content ANSWERED   : {answered}/{len(content)}   (REFUSED on content: {refused_content})")
    print(f"  Trap REFUSED_OK    : {trap_ok}/{len(traps)}   ⚠REVIEW(manual HALLU): {trap_review}")
    print(f"  Errors             : {errors}")
    if lat:
        lat.sort()
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"  Latency ms  p50={int(statistics.median(lat))} p90={lat[int(len(lat)*0.9)-1]} p95={p95} max={max(lat)}")
    print(f"  NOTE: content REFUSED + trap REVIEW need MANUAL read above (HALLU = manual verdict).")
    print(f"{'='*92}\n")


if __name__ == "__main__":
    asyncio.run(main())
