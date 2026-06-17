"""Root-cause diagnostic for multi-step misses (5-step, evidence-driven).

For each diagnostic question it:
  1. Calls the live bot (bypass_cache, debug) → answer + retrieved chunk ids/text.
  2. For each must_contain literal, classifies WHERE the failure is:
       - IN_ANSWER          : literal present in bot answer (ok)
       - IN_CHUNK_NOT_ANSWER: literal in a RETRIEVED chunk but bot omitted it
                              → reasoning / sysprompt (bot had it, didn't use/compute)
       - IN_CORPUS_NOT_CHUNK: literal exists in corpus but NOT retrieved
                              → RETRIEVAL gap
       - NOT_IN_CORPUS       : literal absent from corpus
                              → bad ground-truth (question/expected wrong), NOT a bot bug

This is the decisive layer-attribution: code/retrieval vs sysprompt/reasoning vs
test-design. Number formats are normalised (3.597.000 == 3597000).

Usage: PYTHONPATH=. python scripts/multistep_rootcause.py
"""
from __future__ import annotations

import asyncio
import os
import re

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://localhost:3004/api/ragbot/test"

# Representative failing / partial questions (one per weak area).
CASES = [
    {"bot_id": "lich-su-vn",
     "q": "Tỷ lệ biết chữ Việt Nam tăng từ ~5% năm 1945 lên 95,8% năm 2020. Vì sao giáo dục phát triển vượt bậc sau 1986 so với thời bao cấp, và liên hệ thế nào với chính sách Đổi Mới?",
     "must": ["1945", "95,8%", "Đổi Mới"]},
    {"bot_id": "luat-giao-thong",
     "q": "Nếu một người lái ô tô vượt đèn đỏ VÀ đồng thời có nồng độ cồn ở mức 2 (từ 50mg đến dưới 80mg/100ml máu), tổng mức phạt tiền tối thiểu và tối đa là bao nhiêu?",
     "must": ["4.000.000", "6.000.000", "16.000.000", "18.000.000"]},
    {"bot_id": "thong-tu-09-2020-tt-nhnn",
     "q": "Thông tư 09/2020/TT-NHNN có hiệu lực từ ngày nào và thay thế văn bản nào? Điểm b khoản 4 Điều 20 có hiệu lực muộn hơn bao nhiêu tháng so với ngày hiệu lực chung?",
     "must": ["01/01/2021", "18/2018/TT-NHNN", "01/01/2022"]},
    {"bot_id": "toan-hoc-12",
     "q": "Phương trình az² + bz + c = 0 (hệ số thực) có Δ < 0. Nghiệm là số phức dạng gì và hai nghiệm quan hệ gì? Minh họa z² + 2z + 5 = 0.",
     "must": ["liên hợp", "-1 + 2i", "-1 - 2i"]},
    {"bot_id": "kinh-te-vi-mo",
     "q": "Nền kinh tế suy thoái với khe hẹp âm. Chính phủ tăng chi tiêu G thêm 100 tỷ, MPC = 0,8. GDP thực tế tăng thêm bao nhiêu và gọi là hiệu ứng gì?",
     "must": ["số nhân", "500 tỷ"]},
    {"bot_id": "vat-ly-11",
     "q": "Một cuộn cảm L = 0,1 H mang dòng I = 2 A. Nếu dòng giảm đều về 0 trong 0,05 s, tính suất điện động tự cảm và năng lượng từ trường ban đầu. Giải thích chiều theo định luật Lenz.",
     "must": ["4 V", "0,2 J", "Lenz"]},
]


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"(?<=\d)[.,\s](?=\d)", "", s)
    return re.sub(r"\s+", " ", s)


def _has(hay: str, needle: str) -> bool:
    return _norm(needle) in _norm(hay)


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/tokens/self", timeout=10)
    return r.json()["token"]


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with httpx.AsyncClient() as client:
        for case in CASES:
            tok = await _token(client)
            try:
                r = await client.post(
                    f"{BASE}/chat",
                    json={"bot_id": case["bot_id"], "channel_type": "web",
                          "question": case["q"], "bypass_cache": True},
                    headers={"Authorization": f"Bearer {tok}"}, timeout=120,
                )
                if r.status_code != 200:
                    print(f"\n### [{case['bot_id']}] HTTP {r.status_code}: {r.text[:120]}")
                    continue
                d = r.json()
                p = d.get("data") if isinstance(d, dict) and "data" in d else d
            except Exception as exc:  # noqa: BLE001
                print(f"\n### [{case['bot_id']}] ERROR {type(exc).__name__}: {exc}")
                continue
            p = p or {}
            answer = p.get("answer", "") or ""
            dbg = p.get("debug") or {}
            sources = p.get("sources") or dbg.get("sources") or []
            # pull retrieved chunk text from DB by the cited chunk ids if present,
            # else fall back to source previews returned by the API.
            chunk_blob = " ".join(
                (s.get("preview") or "") + " " + (s.get("content") or "")
                for s in sources
            )

            print(f"\n{'='*70}\n### [{case['bot_id']}] {p.get('answer_type')}  "
                  f"chunks={p.get('chunks_used')} top={dbg.get('top_score')}")
            print(f"Q   : {case['q'][:140]}")
            print(f"BOT : {answer[:280].replace(chr(10),' ')}")
            # classify each literal
            async with eng.connect() as conn:
                for lit in case["must"]:
                    in_ans = _has(answer, lit)
                    in_chunk = _has(chunk_blob, lit)
                    # corpus check: normalise both sides via SQL-side fetch + py compare
                    rows = await conn.execute(text("""
                        SELECT dc.content FROM document_chunks dc
                        JOIN documents d ON d.id = dc.record_document_id
                        JOIN bots b ON b.id = d.record_bot_id
                        WHERE b.bot_id = :bid
                    """).bindparams(bid=case["bot_id"]))
                    in_corpus = any(_has(row[0] or "", lit) for row in rows)
                    if in_ans:
                        verdict = "IN_ANSWER ✅"
                    elif in_chunk:
                        verdict = "IN_CHUNK_NOT_ANSWER → reasoning/sysprompt"
                    elif in_corpus:
                        verdict = "IN_CORPUS_NOT_CHUNK → RETRIEVAL gap"
                    else:
                        verdict = "NOT_IN_CORPUS → bad ground-truth (test design)"
                    print(f"   • {lit!r:24s} {verdict}")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
