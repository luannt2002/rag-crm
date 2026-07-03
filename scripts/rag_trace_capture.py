"""RAG trace capture for Claude-grading (no external LLM judge).

Runs every scenario question against the live /chat with debug=full and dumps a
RICH per-question record — intent, rewritten query, top_k, score_max,
chunks_graded, EVERY retrieved chunk (content+score), and the full answer — to a
JSON the Claude agent then reads and grades SEMANTICALLY (correct / wrong /
refuse / hallu by MEANING, not substring). Replaces the substring/RAGAS-API
grader whose false-fails were shown in RAG_DEEP_TEST_chinh-sach-xe_20260703.md.

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/rag_trace_capture.py \
        --scenario tests/scenarios/chinh-sach-xe-qa20_scenario.json \
        --out reports/rag_trace_chinh-sach-xe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


async def _ask(c, tok, bot, ch, ws, q, cid) -> dict:
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": ws,
            "question": q, "connect_id": cid, "bypass_cache": True, "debug": "full"}
    try:
        r = await c.post(f"{BASE}/api/ragbot/test/chat",
                         headers={"Authorization": f"Bearer {tok}", **_BYPASS},
                         json=body, timeout=180)
        return r.json()
    except Exception as exc:  # noqa: BLE001 — capture harness: record the error, never crash the run
        return {"error": str(exc)}


def _record(q: dict, resp: dict) -> dict:
    dbg = resp.get("debug") or {}
    chunks = resp.get("retrieved_chunks_content") or []
    # Keep full chunk text (capped) + score so the grader sees exactly what the
    # LLM saw and can judge whether the answer WAS supported by retrieval.
    trimmed = [
        {
            "score": ch.get("score") or ch.get("rerank_score") or ch.get("rrf_score"),
            "content": (ch.get("content") or ch.get("text") or "")[:500],
        }
        for ch in chunks[:12]
    ]
    return {
        "id": q["id"],
        "flow": q.get("flow", ""),
        "question": q["q"],
        "expect": q.get("expect"),
        "intent": dbg.get("intent"),
        "rewritten": dbg.get("rewritten_query") or dbg.get("condensed_query"),
        "retrieve_mode": dbg.get("retrieve_mode"),
        # UNAMBIGUOUS field names — the old "top_k" was misread as "chunks to
        # the LLM" when it is actually the RETRIEVAL candidate width.
        "retrieve_candidates_topk": dbg.get("top_k", len(chunks)),  # wide net pulled from DB
        "chunks_to_llm": len(chunks),                                # what ACTUALLY reached the answer LLM
        "chunks_graded_pass": dbg.get("chunks_graded"),              # survived CRAG grading
        "score_max": dbg.get("score_max"),
        "answer": resp.get("answer") or "",
        "answer_type": resp.get("answer_type"),
        "chunks": trimmed,
        "error": resp.get("error"),
    }


async def main(scenario: str, out: str, concurrency: int) -> None:
    sc = json.load(open(scenario))
    bot, ch, ws = sc["bot_id"], sc["channel_type"], sc.get("workspace_id", "")
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as c:
        tok = await _token(c)

        async def _one(q):
            async with sem:
                resp = await _ask(c, tok, bot, ch, ws, q["q"], f"trace-{q['id']}")
                return _record(q, resp)

        records = await asyncio.gather(*[_one(q) for q in sc["questions"]])

    payload = {"bot_id": bot, "scenario": scenario, "n": len(records),
               "records": sorted(records, key=lambda r: r["id"])}
    with open(out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"captured {len(records)} → {out}")
    # brief console preview — CANDIDATES vs TO-LLM made explicit
    print(f"  {'id':<6}{'intent':<12}{'candidates':>11}{'→to_LLM':>9}{'graded':>7}  answer")
    for r in payload["records"]:
        print(f"  {r['id']:<6}{str(r['intent']):<12}"
              f"{str(r['retrieve_candidates_topk']):>11}{str(r['chunks_to_llm']):>9}"
              f"{str(r['chunks_graded_pass']):>7}  {r['answer'][:50]!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()
    asyncio.run(main(a.scenario, a.out, a.concurrency))
