"""READ-ONLY forensic: for weak-precision questions, expose for each USED chunk:
rerank score (live API), cosine(query,chunk) from DB vectors, dense-rank over the
whole document. Classifies each used chunk relevant/noise vs gold facts. No core change."""
from __future__ import annotations
import asyncio, json, os
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://localhost:3004/api/ragbot/test"
ZE_URL = "https://api.zeroentropy.dev/v1/models/embed"
ZE_KEY = os.environ.get("ZEROENTROPY_EMBEDDING_API_KEY") or os.environ["ZEROENTROPY_API_KEY"]

# (bot, question, gold-fact substrings that mark a chunk "relevant/correct")
CASES = [
    ("thong-tu-09-2020-tt-nhnn",
     "Hệ thống thông tin cấp độ 3 được xác định dựa trên tiêu chí nào? Nếu một hệ thống phục vụ trên 10.000 người thì thuộc cấp độ mấy?",
     ["cấp độ 3", "10.000", "ngừng vận hành"]),
    ("test-spa-id",
     "Dịch vụ chăm sóc da chuyên sâu có bao nhiêu bước quy trình chuẩn y khoa? So với dịch vụ trị mụn chuyên sâu thì chênh nhau mấy bước?",
     ["10 bước", "chuẩn y khoa", "trị mụn"]),
    ("luat-giao-thong",
     "Đối với ô tô, nếu để người ngồi trên xe không thắt dây an toàn và đồng thời đỗ xe trên đường cao tốc thì tổng mức phạt tối thiểu là bao nhiêu?",
     ["thắt dây", "800.000", "cao tốc", "đỗ xe"]),
]


async def _ze_embed(c, q):
    r = await c.post(ZE_URL, json={"model": "zembed-1", "input": [q],
                                   "input_type": "query", "dimensions": 1280},
                     headers={"Authorization": f"Bearer {ZE_KEY}"}, timeout=60)
    r.raise_for_status()
    return (r.json().get("results") or [{}])[0].get("embedding")


def _hit(content, facts):
    cl = content.lower().replace(".", "").replace(",", "")
    return [f for f in facts if f.lower().replace(".", "").replace(",", "") in cl]


async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    async with httpx.AsyncClient() as c:
        for bot, q, facts in CASES:
            print("\n" + "=" * 90)
            print(f"[{bot}] {q[:80]}")
            print(f"gold-fact mốc: {facts}")
            # 1) live pipeline → 4 used chunks (post-rerank)
            t = (await c.get(f"{BASE}/tokens/self", timeout=10)).json()["token"]
            r = await c.post(f"{BASE}/chat",
                             json={"bot_id": bot, "channel_type": "web", "question": q,
                                   "bypass_cache": True, "debug": "full"},
                             headers={"Authorization": f"Bearer {t}"}, timeout=180)
            d = r.json().get("data", r.json())
            used = d.get("retrieved_chunks_content") or []
            dbg = d.get("debug", {})
            print(f"chunks_used={d.get('chunks_used')} top_rerank={d.get('top_score')} "
                  f"top_k={dbg.get('top_k')} chunks_graded={dbg.get('chunks_graded')} "
                  f"score_min/avg/max={dbg.get('score_min')}/{dbg.get('score_avg')}/{dbg.get('score_max')}")
            # 2) embed query + dense cosine over whole doc
            qv = await _ze_embed(c, q)
            qstr = "[" + ",".join(str(x) for x in qv) + "]"
            async with engine.connect() as cx:
                rows = (await cx.execute(text("""
                    SELECT dc.id::text, dc.content, 1-(dc.embedding <=> CAST(:qv AS vector)) AS cos
                    FROM document_chunks dc JOIN documents doc ON doc.id=dc.record_document_id
                    JOIN bots b ON b.id=doc.record_bot_id
                    WHERE b.bot_id=:b AND dc.embedding IS NOT NULL
                    ORDER BY dc.embedding <=> CAST(:qv AS vector) ASC"""),
                    {"qv": qstr, "b": bot})).fetchall()
            dense = [(rid, cos, cont) for rid, cont, cos in rows]   # already sorted by cosine desc
            dense_rank = {rid: i + 1 for i, (rid, _, _) in enumerate(dense)}
            cos_by_id = {rid: cos for rid, cos, _ in dense}
            print(f"\n  USED chunks (rerank order) — rerank_score | cosine | dense_rank | gold-hits:")
            for j, ch in enumerate(used):
                cid = ch.get("chunk_id")
                cont = (ch.get("content") or "")
                hits = _hit(cont, facts)
                tag = "✅CORRECT" if hits else "⚠️NOISE"
                print(f"   rerank#{j+1} {tag} rerank={ch.get('score'):.3f} "
                      f"cos={cos_by_id.get(cid,0):.3f} dense_rank={dense_rank.get(cid,'?')} hits={hits}")
                print(f"      {cont[:200].replace(chr(10),' ')}")
            # 3) dense top-6 for comparison (what pure embedding would pick)
            print(f"\n  DENSE top-6 (pure embedding cosine) — cosine | gold-hits:")
            for i, (rid, cos, cont) in enumerate(dense[:6]):
                hits = _hit(cont, facts)
                print(f"   dense#{i+1} cos={cos:.3f} {'✅' if hits else '·'} hits={hits} :: {cont[:120].replace(chr(10),' ')}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
