#!/usr/bin/env python3
"""Verify the rewritten 3-bot sysprompts: greeting/identity, list-all,
off-topic refusal, HALLU traps, aggregation. Captures answer_type (blocked)."""
from __future__ import annotations
import asyncio, os, sys
import httpx
BASE=os.getenv("RAGBOT_BASE_URL","http://localhost:3004")
_BY={"X-Loadtest-Bypass":os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN","")}
# (bot, ws, kind, question)
CASES=[
 ("test-spa-id","spa","greet","hi shop"),
 ("test-spa-id","spa","identity","bạn là ai?"),
 ("test-spa-id","spa","list-cat","tư vấn về da cho mình"),
 ("test-spa-id","spa","multi","tẩy da chết có mấy loại?"),
 ("test-spa-id","spa","price","trị mụn giá bao nhiêu?"),
 ("test-spa-id","spa","agg","dịch vụ nào đắt nhất?"),
 ("test-spa-id","spa","offtopic","viết cho tôi 1 web html"),
 ("test-spa-id","spa","offtopic2","code game bắn chim đi"),
 ("test-spa-id","spa","hallu","phun xăm chân mày giá bao nhiêu?"),
 ("chinh-sach-xe","xe","greet","chào shop"),
 ("chinh-sach-xe","xe","identity","bạn là ai vậy?"),
 ("chinh-sach-xe","xe","warranty","bảo hành lốp thế nào?"),
 ("chinh-sach-xe","xe","tire","lốp 195/65R15 còn hàng không?"),
 ("chinh-sach-xe","xe","brand-hallu","có lốp Michelin không?"),
 ("chinh-sach-xe","xe","offtopic","code game cho tôi"),
 ("thong-tu-09-2020-tt-nhnn","legal","orient","bạn là ai? tài liệu này về gì?"),
 ("thong-tu-09-2020-tt-nhnn","legal","summary","tóm tắt nội dung chính, tôi hỏi được gì?"),
 ("thong-tu-09-2020-tt-nhnn","legal","lookup","Điều 56 quy định gì?"),
 ("thong-tu-09-2020-tt-nhnn","legal","lookup2","thời hạn báo cáo sự cố là bao lâu?"),
 ("thong-tu-09-2020-tt-nhnn","legal","offtopic-law","luật an ninh mạng quy định gì?"),
 ("thong-tu-09-2020-tt-nhnn","legal","offtopic","viết code cho tôi"),
 ("thong-tu-09-2020-tt-nhnn","legal","hallu","Điều 78 quy định gì?"),
]
async def tok(c):
    r=await c.get(f"{BASE}/api/ragbot/test/tokens/self",headers=_BY,timeout=30);r.raise_for_status()
    d=r.json();return d.get("token") or d.get("access_token") or d["data"]["token"]
async def main():
    async with httpx.AsyncClient() as c:
        t=await tok(c);h={"Authorization":f"Bearer {t}","Content-Type":"application/json",**_BY}
        for bot,ws,kind,q in CASES:
            body={"bot_id":bot,"channel_type":"web","workspace_id":ws,"question":q,"connect_id":f"v-{kind}","bypass_cache":True}
            try:
                r=await c.post(f"{BASE}/api/ragbot/test/chat",headers=h,json=body,timeout=60);d=r.json()
            except Exception as e: d={"error":str(e)}
            a=d.get("answer") or (d.get('data') or {}).get('answer') or d.get("error","")
            bl="[BLOCKED]" if d.get("answer_type")=="blocked" else ""
            print(f"[{ws:>5}|{kind:>12}] {bl} {q[:34]}")
            print(f"     → {a[:150]}")
asyncio.run(main())
