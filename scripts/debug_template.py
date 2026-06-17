"""LAYER-ISOLATION DEBUG TEMPLATE — soi mỗi câu hỏi rớt ở tầng nào.

Với mỗi câu có vấn đề, truy vết gold-fact qua 5 tầng pipeline thật:
  L0 CORPUS   — fact có trong tài liệu không?           (in_corpus)
  L1 RETRIEVE — chunk chứa fact có vào dense top-K?       (dense_rank ≤ top_k)
  L2 RERANK   — chunk đó có sống sau rerank-filter?       (in chunks_used)
  L3 LLM-USE  — LLM có dùng fact trong câu trả lời?       (in_answer)
  L4 METRIC   — judge chấm điểm gì                        (scores)

Phân loại tầng FAIL (cây quyết định):
  ¬corpus            → L0 CORPUS GAP   (bot đúng khi không trả — tài liệu thiếu)
  corpus ¬dense_topk → L1 RETRIEVE MISS (embedding/BM25 không kéo lên top-K)
  dense_topk ¬used   → L2 RERANK CUT   (vào top-K nhưng filter loại)
  used ¬answer       → L3 LLM UNDER-USE(LLM có chunk nhưng trả thiếu/sai)
  answer ok, judge ↓ → L4 METRIC/QUALITY (artifact đo, hoặc answer lệch)

Usage: PYTHONPATH=. python scripts/debug_template.py --model gpt-4.1 --top-k 20
Đọc reports/MODEL_MATRIX_<model>.json, tự lấy câu có cờ, ghi reports/DEBUG_LAYER_TRACE.md
"""
from __future__ import annotations
import argparse, asyncio, json, os, re
from pathlib import Path
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).parent.parent
BASE = "http://localhost:3004/api/ragbot/test"
ZE_URL = "https://api.zeroentropy.dev/v1/models/embed"
ZE_KEY = os.environ.get("ZEROENTROPY_EMBEDDING_API_KEY") or os.environ.get("ZEROENTROPY_API_KEY")


def _norm(s: str) -> str:
    return re.sub(r"[.,\s]", "", str(s).lower())


def _in(fact: str, hay: str) -> bool:
    nf, nh = _norm(fact), _norm(hay)
    if not nf:
        return True
    if re.fullmatch(r"\d[\d.]*", fact.strip()):   # số: so khớp chuỗi số đã chuẩn hoá
        return _norm(fact) in nh
    toks = [t for t in re.split(r"\s+", fact.lower()) if len(t) > 1]
    return sum(1 for t in toks if t in hay.lower()) >= max(1, (len(toks) + 1) // 2) if toks else nf in nh


async def _token(c):
    return (await c.get(f"{BASE}/tokens/self", timeout=10)).json()["token"]


async def _ask(c, bot, q):
    for att in range(4):
        t = await _token(c)
        r = await c.post(f"{BASE}/chat", json={"bot_id": bot, "channel_type": "web",
                         "question": q, "bypass_cache": True, "debug": "full"},
                         headers={"Authorization": f"Bearer {t}"}, timeout=180)
        if r.status_code == 503:
            await asyncio.sleep(4 * (att + 1)); continue
        if r.status_code != 200:
            return None
        return r.json().get("data", r.json())
    return None


async def _ze(c, q):
    r = await c.post(ZE_URL, json={"model": "zembed-1", "input": [q],
                     "input_type": "query", "dimensions": 1280},
                     headers={"Authorization": f"Bearer {ZE_KEY}"}, timeout=60)
    r.raise_for_status()
    return (r.json().get("results") or [{}])[0].get("embedding")


def _classify(corpus, dense_topk, used, answer, scores):
    if not corpus:
        return "L0 CORPUS-GAP", "fact không có trong tài liệu → bot đúng khi không trả"
    if not dense_topk:
        return "L1 RETRIEVE-MISS", "chunk chứa fact KHÔNG vào dense top-K (embedding/bm25 ranking)"
    if not used:
        return "L2 RERANK-CUT", "vào top-K nhưng rerank-filter loại mất"
    if not answer:
        return "L3 LLM-UNDER-USE", "LLM CÓ chunk nhưng trả thiếu/sai (sysprompt thận trọng?)"
    f = scores.get("faithfulness")
    if f is not None and f < 0.8:
        return "L4 METRIC/QUALITY", "answer có fact nhưng judge chấm thấp → artifact đo hoặc answer lệch"
    return "OK", "không thấy vấn đề tầng (false flag)"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4.1")
    ap.add_argument("--top-k", type=int, default=20)
    args = ap.parse_args()

    data = json.loads((ROOT / "reports" / f"MODEL_MATRIX_{args.model}.json").read_text(encoding="utf-8"))
    # pick câu có cờ thật (bỏ false-pos chỉ-suspected-numeric)
    probs = []
    for d in data["documents"]:
        for q in d["questions"]:
            s = q["scores"]
            ar, f, cr = s.get("answer_relevancy"), s.get("faithfulness"), s.get("contextual_recall")
            flag = (q.get("request_fail") or (ar is not None and ar < 0.8)
                    or (f is not None and f < 0.8) or (cr is not None and cr < 0.5))
            if flag:
                probs.append((d["bot"], q))

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    chunk_cache = {}

    async def _doc_chunks(cx, bot):
        if bot not in chunk_cache:
            rows = (await cx.execute(text("""SELECT dc.content FROM document_chunks dc
                JOIN documents d ON d.id=dc.record_document_id JOIN bots b ON b.id=d.record_bot_id
                WHERE b.bot_id=:b AND dc.embedding IS NOT NULL"""), {"b": bot})).fetchall()
            chunk_cache[bot] = [r[0] or "" for r in rows]
        return chunk_cache[bot]

    out = ["# LAYER-ISOLATION DEBUG — câu có vấn đề rớt ở tầng nào (2026-06-11)", "",
           f"> Model {args.model} · top_k={args.top_k} · pipeline thật · gold-fact truy vết 5 tầng.", "",
           "| Bot | Câu | Fact thiếu | L0 corpus | L1 dense-rank | L2 used | L3 answer | **TẦNG FAIL** |",
           "|---|---|---|---|---|---|---|---|"]
    counts = {}
    async with httpx.AsyncClient() as c:
        for bot, q in probs:
            facts = q.get("reference_facts") or []
            d = await _ask(c, bot, q["question"])
            if d is None:
                out.append(f"| {bot} | {q['id'].split('_')[-1]} | — | — | — | — | — | **OPS FAIL (timeout)** |")
                counts["OPS"] = counts.get("OPS", 0) + 1
                continue
            ans = d.get("answer", "") or ""
            used_ctx = " ".join([(s.get("preview") or "") for s in (d.get("sources") or [])]
                                + [ch.get("content", "") if isinstance(ch, dict) else str(ch)
                                   for ch in (d.get("retrieved_chunks_content") or [])])
            # embed query + cosine rank toàn doc
            async with engine.connect() as cx:
                chunks = await _doc_chunks(cx, bot)
                qv = await _ze(c, q["question"])
                qstr = "[" + ",".join(str(x) for x in qv) + "]"
                ranked = (await cx.execute(text("""
                    SELECT dc.content, 1-(dc.embedding <=> CAST(:qv AS vector)) cos
                    FROM document_chunks dc JOIN documents d ON d.id=dc.record_document_id
                    JOIN bots b ON b.id=d.record_bot_id WHERE b.bot_id=:b AND dc.embedding IS NOT NULL
                    ORDER BY dc.embedding <=> CAST(:qv AS vector) ASC"""), {"qv": qstr, "b": bot})).fetchall()
            corpus_all = "\n".join(chunks)
            # chọn fact "khó nhất" còn thiếu để định vị tầng
            worst = None
            for fct in facts:
                in_corp = _in(fct, corpus_all)
                # chunk chứa fact + dense rank của nó
                drank = None
                for i, (cont, _) in enumerate(ranked):
                    if _in(fct, cont):
                        drank = i + 1; break
                in_topk = drank is not None and drank <= args.top_k
                in_used = _in(fct, used_ctx)
                in_ans = _in(fct, ans)
                state = (in_corp, in_topk, in_used, in_ans)
                # ưu tiên fact fail sớm nhất (corpus<dense<used<answer)
                sev = (0 if not in_corp else 1 if not in_topk else 2 if not in_used else 3 if not in_ans else 4)
                if worst is None or sev < worst[0]:
                    worst = (sev, fct, in_corp, drank, in_topk, in_used, in_ans)
            if worst is None:
                worst = (4, "(no facts)", True, 1, True, True, True)
            _, fct, in_corp, drank, in_topk, in_used, in_ans = worst
            layer, _ = _classify(in_corp, in_topk, in_used, in_ans, q["scores"])
            counts[layer.split()[0]] = counts.get(layer.split()[0], 0) + 1
            ck = lambda b: "✅" if b else "❌"
            out.append(f"| {bot} | {q['id'].split('_')[-1]} | {fct[:24]} | {ck(in_corp)} | "
                       f"{'#'+str(drank) if drank else '∅'}{'✅' if in_topk else '❌'} | {ck(in_used)} | "
                       f"{ck(in_ans)} | **{layer}** |")
            print(f"  {bot} {q['id']}: {layer}  (fact={fct[:30]})", flush=True)
    out += ["", "## Tổng hợp tầng FAIL", ""]
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        out.append(f"- **{k}**: {v} câu")
    (ROOT / "reports" / "DEBUG_LAYER_TRACE.md").write_text("\n".join(out), encoding="utf-8")
    await engine.dispose()
    print("\nWROTE reports/DEBUG_LAYER_TRACE.md")
    print("COUNTS:", counts)


if __name__ == "__main__":
    asyncio.run(main())
