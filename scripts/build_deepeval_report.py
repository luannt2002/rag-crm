"""DeepEval RAG eval (4 metrics) over live bots → which metric is "lệch" (<80%).

For each template question (live, bypass_cache):
  capture input / actual_output / retrieval_context (sources[].preview) and
  build a DeepEval LLMTestCase, then score the 4 native RAG metrics:
    - AnswerRelevancyMetric   → answer addresses the question
    - FaithfulnessMetric      → answer grounded in retrieved context (no fabrication)
    - ContextualPrecisionMetric → relevant chunks ranked above noise (reranker)
    - ContextualRecallMetric  → retrieved context covers the expected answer (retrieval/chunking)

Judge = gpt-4.1 (STRONGER than the bot's gpt-4.1-mini) at temperature 0, per the
LLM-as-judge rule (judge must beat the system it grades).

Incremental save per bot (survives kills) + resume. SEM kept low (RAM-safe).

Usage:
  PYTHONPATH=. python scripts/build_deepeval_report.py --sample 3   # smoke 3 q/bot, 1 bot
  PYTHONPATH=. python scripts/build_deepeval_report.py              # full
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
QFILE = Path(__file__).parent / "multistep_questions.json"
BASE = "http://localhost:3004/api/ragbot/test"
JUDGE_MODEL = "gpt-4.1"            # stronger than bot (gpt-4.1-mini); temp=0 set by DeepEval
SEM = asyncio.Semaphore(3)


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/tokens/self", timeout=10)
    return r.json()["token"]


async def _ask(c: httpx.AsyncClient, bot: str, q: str) -> dict:
    for attempt in range(4):
        tok = await _token(c)
        r = await c.post(f"{BASE}/chat",
                         json={"bot_id": bot, "channel_type": "web", "question": q, "bypass_cache": True},
                         headers={"Authorization": f"Bearer {tok}"}, timeout=120)
        if r.status_code == 503:
            await asyncio.sleep(3 * (attempt + 1)); continue
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}"}
        d = r.json()
        return d.get("data") if isinstance(d, dict) and "data" in d else d
    return {"_error": "503"}


def _context(d: dict) -> list[str]:
    out = []
    for s in (d.get("sources") or []):
        p = (s.get("preview") or "").strip()
        if p:
            out.append(p)
    for cit in (d.get("citations") or []):
        q = (cit.get("quote") or "").strip()
        if q:
            out.append(q)
    return out or ["(không có context — retrieval rỗng)"]


async def _capture(c: httpx.AsyncClient, bot: str, g: dict) -> dict:
    async with SEM:
        d = await _ask(c, bot, g["question"])
    ans = d.get("answer", "") if "_error" not in d else ""
    # Reference: natural-ish expected answer from the gold facts (pragmatic;
    # over-specification caveat applies to ContextualRecall/Precision only).
    ref = "Câu trả lời cần nêu: " + ", ".join(g.get("must_contain", []))
    return {
        "input": g["question"],
        "actual_output": ans or "(bot không trả lời)",
        "retrieval_context": _context(d),
        "expected_output": ref,
        "n_context": len(d.get("sources") or []),
    }


def _eval_case(tc_data: dict) -> dict:
    from deepeval.metrics import (
        AnswerRelevancyMetric, FaithfulnessMetric,
        ContextualPrecisionMetric, ContextualRecallMetric,
    )
    from deepeval.test_case import LLMTestCase
    tc = LLMTestCase(
        input=tc_data["input"],
        actual_output=tc_data["actual_output"],
        retrieval_context=tc_data["retrieval_context"],
        expected_output=tc_data["expected_output"],
    )
    out = {}
    reasons = {}
    for key, MetricCls in [
        ("answer_relevancy", AnswerRelevancyMetric),
        ("faithfulness", FaithfulnessMetric),
        ("contextual_precision", ContextualPrecisionMetric),
        ("contextual_recall", ContextualRecallMetric),
    ]:
        try:
            # async_mode=True → the metric's internal judge calls run concurrently.
            m = MetricCls(model=JUDGE_MODEL, threshold=0.8, async_mode=True)
            m.measure(tc)
            out[key] = round(m.score, 3) if m.score is not None else None
            # capture WHY (reason) for low scores → "yếu ở đâu"
            r = getattr(m, "reason", None)
            if r and out[key] is not None and out[key] < 0.8:
                reasons[key] = str(r)[:300]
        except Exception as exc:  # noqa: BLE001 — one metric failing must not kill the row
            out[key] = None
            out[f"{key}_err"] = str(exc)[:120]
    if reasons:
        out["_reasons"] = reasons
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="N questions/bot smoke (0=full)")
    ap.add_argument("--bots", nargs="*", help="restrict to these bot ids")
    ap.add_argument("--date", default="20260611")
    args = ap.parse_args()

    gold = json.loads(QFILE.read_text(encoding="utf-8"))
    by_bot: dict[str, list[dict]] = {}
    for g in gold:
        by_bot.setdefault(g["bot_id"], []).append(g)
    if args.bots:
        by_bot = {b: by_bot[b] for b in args.bots if b in by_bot}

    out_path = ROOT / "reports" / f"DEEPEVAL_REPORT_{args.date}.json"
    docs = []
    done = set()
    if out_path.exists() and not args.sample:
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            docs = prev.get("documents", [])
            done = {d["bot"] for d in docs}
        except (ValueError, KeyError):
            pass

    def _save():
        agg = {k: [] for k in ("answer_relevancy", "faithfulness", "contextual_precision", "contextual_recall")}
        for d in docs:
            for q in d["questions"]:
                for k in agg:
                    if q["scores"].get(k) is not None:
                        agg[k].append(q["scores"][k])
        overall = {k: round(sum(v) / len(v), 3) if v else None for k, v in agg.items()}
        out_path.write_text(json.dumps({
            "judge_model": JUDGE_MODEL,
            "overall": overall,
            "documents": docs,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return overall

    async with httpx.AsyncClient() as c:
        for bot, golds in by_bot.items():
            if bot in done:
                continue
            qs = golds[: args.sample] if args.sample else golds
            captured = await asyncio.gather(*[_capture(c, bot, g) for g in qs])
            # Grade questions CONCURRENTLY (judge calls are I/O-bound) — bounded.
            esem = asyncio.Semaphore(4)
            async def _grade(i: int, cap: dict) -> dict:
                async with esem:
                    sc = await asyncio.to_thread(_eval_case, cap)
                print(f"  {bot} q{i+1}: AR={sc.get('answer_relevancy')} "
                      f"F={sc.get('faithfulness')} CP={sc.get('contextual_precision')} "
                      f"CR={sc.get('contextual_recall')}", flush=True)
                return {"id": f"{bot}_q{i+1:02d}", **cap, "scores": sc}
            rows = await asyncio.gather(*[_grade(i, cap) for i, cap in enumerate(captured)])
            per = {k: [r["scores"][k] for r in rows if r["scores"].get(k) is not None]
                   for k in ("answer_relevancy", "faithfulness", "contextual_precision", "contextual_recall")}
            bot_avg = {k: round(sum(v) / len(v), 3) if v else None for k, v in per.items()}
            docs.append({"bot": bot, "n": len(rows), "avg": bot_avg, "questions": rows})
            print(f"  → {bot} AVG: {bot_avg}", flush=True)
            if not args.sample:
                _save()
    overall = _save()
    print(f"\nwrote {out_path}\nOVERALL (judge {JUDGE_MODEL}): {overall}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
