"""Per-model worker: capture (QA_FORMAT) + 4 DeepEval RAGAS metrics + exact-match.

For ONE answer model (already wired in the live pipeline), runs all 12 bots ×
10 questions SEQUENTIALLY (throttled, sleep between requests → no ZE/OpenAI 429),
captures the QA_FORMAT detail + scores the 4 DeepEval RAG metrics with a FIXED
independent judge (gpt-5.4, ≠ the candidate models → no self-grading), plus a
deterministic numeric exact-match. Incremental per-bot save + resume.

Usage: PYTHONPATH=. python scripts/capture_score_model.py --model gpt-4.1 --sleep 1.0
Writes reports/MODEL_MATRIX_<model>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).parent.parent
QFILE = Path(__file__).parent / "multistep_questions.json"
BASE = "http://localhost:3004/api/ragbot/test"
JUDGE_MODEL = "gpt-5.5"   # fixed independent judge (user choice)
_NUM = re.compile(r"\b\d{1,3}(?:\.\d{3})+\b|\b\d{4,}\b|\b\d{1,3}/\d{4}\b")


def _norm(s: str) -> str:
    return re.sub(r"[.,\s]", "", str(s).lower())


def _fact_in(fact: str, hay: str) -> bool:
    nf, nh = _norm(fact), _norm(hay)
    if not nf:
        return True
    if re.fullmatch(r"\d+", nf):
        return nf in nh
    if nf in nh:
        return True
    toks = [t for t in re.split(r"\s+", fact.lower()) if len(t) > 1]
    return (sum(1 for t in toks if t in hay.lower()) >= max(1, (len(toks) + 1) // 2)) if toks else nf in nh


async def _token(c):
    r = await c.get(f"{BASE}/tokens/self", timeout=10)
    return r.json()["token"]


async def _ask(c, bot, q):
    for attempt in range(5):
        t = await _token(c)
        r = await c.post(f"{BASE}/chat",
                         json={"bot_id": bot, "channel_type": "web", "question": q,
                               "bypass_cache": True, "debug": "full"},
                         headers={"Authorization": f"Bearer {t}"}, timeout=180)
        if r.status_code == 503:
            await asyncio.sleep(5 * (attempt + 1)); continue
        if r.status_code != 200:
            return None
        return r.json().get("data", r.json())
    return None


def _context(d):
    out = []
    for s in (d.get("sources") or []):
        p = (s.get("preview") or "").strip()
        if p:
            out.append(p)
    # debug=full exposes real chunk text
    for ch in (d.get("retrieved_chunks_content") or d.get("retrieved_chunks") or []):
        t = ch if isinstance(ch, str) else (ch.get("content") or ch.get("text") or "")
        if t and t.strip():
            out.append(t.strip())
    return out or ["(retrieval rỗng)"]


_METRICS = {
    "answer_relevancy": "AnswerRelevancyMetric",
    "faithfulness": "FaithfulnessMetric",
    "contextual_precision": "ContextualPrecisionMetric",
    "contextual_recall": "ContextualRecallMetric",
}


def _single_metric(tc_data, key):
    """Score ONE metric (runs in its own thread → 4 metrics go concurrent)."""
    import deepeval.metrics as M
    from deepeval.test_case import LLMTestCase
    tc = LLMTestCase(
        input=tc_data["input"], actual_output=tc_data["actual_output"] or "(rỗng)",
        retrieval_context=tc_data["retrieval_context"], expected_output=tc_data["expected_output"],
    )
    try:
        m = getattr(M, _METRICS[key])(model=JUDGE_MODEL, threshold=0.8, async_mode=False)
        m.measure(tc)
        score = round(m.score, 3) if m.score is not None else None
        reason = getattr(m, "reason", None)
        return key, score, (str(reason)[:300] if reason and score is not None and score < 0.8 else None)
    except Exception as exc:  # noqa: BLE001
        return key, None, f"ERR:{str(exc)[:120]}"


async def _eval_case(tc_data):
    # 4 metrics CONCURRENTLY (each in a thread → 4 parallel gpt-5.5 calls).
    results = await asyncio.gather(*[asyncio.to_thread(_single_metric, tc_data, k) for k in _METRICS])
    out, reasons = {}, {}
    for key, score, reason in results:
        out[key] = score
        if reason and reason.startswith("ERR:"):
            out[f"{key}_err"] = reason[4:]
        elif reason:
            reasons[key] = reason
    if reasons:
        out["_reasons"] = reasons
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--bots", default="", help="comma-separated bot_id subset (eval scope); empty = all")
    ap.add_argument("--tag", default="", help="suffix for output filename (e.g. rules-old / rules-new)")
    args = ap.parse_args()

    bot_filter = {b.strip() for b in args.bots.split(",") if b.strip()}
    gold = json.loads(QFILE.read_text(encoding="utf-8"))
    by_bot = {}
    for g in gold:
        if bot_filter and g["bot_id"] not in bot_filter:
            continue
        by_bot.setdefault(g["bot_id"], []).append(g)

    _suffix = f"_{args.tag}" if args.tag else ""
    out_path = ROOT / "reports" / f"MODEL_MATRIX_{args.model}{_suffix}.json"
    docs, done_q = [], set()
    doc_by_bot = {}
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            docs = prev.get("documents", [])
            for d in docs:
                doc_by_bot[d["bot"]] = d
                for q in d["questions"]:
                    done_q.add(q["id"])           # resume at QUESTION granularity
            print(f"resume: {len(done_q)} câu đã có", flush=True)
        except (ValueError, KeyError):
            pass

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    corpus = {}

    async def _corpus(bot):
        if bot not in corpus:
            async with engine.connect() as cx:
                rows = await cx.execute(text("""SELECT dc.content FROM document_chunks dc
                    JOIN documents d ON d.id=dc.record_document_id JOIN bots b ON b.id=d.record_bot_id
                    WHERE b.bot_id=:b"""), {"b": bot})
                corpus[bot] = "\n".join(r[0] or "" for r in rows.fetchall())
        return corpus[bot]

    def _save():
        agg = {k: [] for k in ("answer_relevancy", "faithfulness", "contextual_precision", "contextual_recall")}
        for d in docs:
            for q in d["questions"]:
                for k in agg:
                    if q["scores"].get(k) is not None:
                        agg[k].append(q["scores"][k])
        overall = {k: round(sum(v) / len(v), 3) if v else None for k, v in agg.items()}
        out_path.write_text(json.dumps({
            "model": args.model, "judge": JUDGE_MODEL, "overall": overall,
            "n_questions": sum(len(d["questions"]) for d in docs), "documents": docs,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _recompute_avgs():
        for d in docs:
            per = {k: [q["scores"][k] for q in d["questions"] if q["scores"].get(k) is not None]
                   for k in ("answer_relevancy", "faithfulness", "contextual_precision", "contextual_recall")}
            d["avg"] = {k: round(sum(v) / len(v), 3) if v else None for k, v in per.items()}
            d["n"] = len(d["questions"])

    esem = asyncio.Semaphore(2)        # parallel câu × 4 metric song song = ~8 judge call
    save_lock = asyncio.Lock()

    async def _one(c, bot, corp, doc, g, i):
        try:
            await _one_inner(c, bot, corp, doc, g, i)
        except Exception as exc:  # noqa: BLE001 — 1 câu lỗi KHÔNG được giết cả worker
            print(f"  [{args.model}] {bot} q{i+1} ERR: {str(exc)[:120]}", flush=True)

    async def _one_inner(c, bot, corp, doc, g, i):
        qid = f"{bot}_q{i+1:02d}"
        if qid in done_q:
            return
        async with esem:
            d = await _ask(c, bot, g["question"])
        if d is None:
            row = {"id": qid, "question": g["question"], "answer": "",
                   "request_fail": True, "scores": {}}
        else:
            ans = d.get("answer", "") or ""
            ctx = _context(d)
            facts = g.get("must_contain", [])
            fact_rows = [{"fact": f, "in_corpus": _fact_in(f, corp),
                          "in_answer": _fact_in(f, ans),
                          "in_retrieved": _fact_in(f, " ".join(ctx))} for f in facts]
            sus = [t for t in set(_NUM.findall(ans)) if _norm(t) not in _norm(corp)]
            tc = {"input": g["question"], "actual_output": ans, "retrieval_context": ctx,
                  "expected_output": "Câu trả lời cần nêu: " + ", ".join(facts)}
            async with esem:
                scores = await _eval_case(tc)            # 4 metric song song bên trong
            row = {"id": qid, "type": g.get("type", ""), "question": g["question"],
                   "answer": ans, "retrieval_context": [x[:400] for x in ctx[:6]],
                   "reference_facts": facts, "db_verify": fact_rows,
                   "suspected_numeric": sus, "request_fail": False, "scores": scores}
            print(f"  [{args.model}] {bot} q{i+1}: AR={scores.get('answer_relevancy')} "
                  f"F={scores.get('faithfulness')} CP={scores.get('contextual_precision')} "
                  f"CR={scores.get('contextual_recall')} | bịa={sus}", flush=True)
        async with save_lock:
            doc["questions"].append(row); done_q.add(qid)
            _recompute_avgs(); _save()

    async with httpx.AsyncClient() as c:
        for bot, golds in by_bot.items():
            doc = doc_by_bot.get(bot)
            if doc is None:
                doc = {"bot": bot, "n": 0, "avg": {}, "questions": []}
                doc_by_bot[bot] = doc; docs.append(doc)
            corp = await _corpus(bot)
            await asyncio.gather(*[_one(c, bot, corp, doc, g, i) for i, g in enumerate(golds)], return_exceptions=True)
            print(f"  → [{args.model}] {bot} DONE", flush=True)
    _recompute_avgs(); _save()
    await engine.dispose()
    print(f"[{args.model}] WROTE {out_path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
