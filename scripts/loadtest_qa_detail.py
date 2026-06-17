"""Load-test harness that LOGS every layer per question — no LLM judge.

For each scenario question it captures the full evidence chain so a human (or a
Claude-Code agent reading the JSON) can score each turn WITHOUT guessing:

  question → reference_facts (golden must-contain) → answer (bot output) →
  top_chunks_retrieved (doc/index/score/preview — what reached the LLM) →
  answer_source_chunk (the corpus chunk that actually holds the answer, found
  via DB even if NOT retrieved) → tokens (prompt/completion/cached) → latency →
  intent / decomposed / cache_status → a DETERMINISTIC prelim verdict + fail_step.

The scoring fields ``claude_verdict`` / ``claude_notes`` are left EMPTY on
purpose — a Claude-Code agent fills them by reading each record (the owner's
"no ChatGPT scoring; let the agent read each Q" rule). The prelim verdict is a
rule-based hint, not the final grade.

Failure layers (fail_step), pinned from evidence — never guessed:
  RETRIEVAL — answer-bearing chunk exists in corpus but NOT in top-K
  FILTER    — chunk retrieved but dropped by the grader (chunks_graded == 0)
  GENERATION— answer chunk reached the LLM but the answer omits/mis-states it
  HALLU     — a trap was answered (ungrounded)
  DATA      — corpus has no answer-bearing chunk at all (golden may be stale)

Writes one JSON per bot: reports/LOADTEST_<bot>_<stamp>.json
(mirrors reports/QA_FORMAT_REPORT_*.json field shape).

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/loadtest_qa_detail.py --stamp 20260613
    .venv/bin/python scripts/loadtest_qa_detail.py --bot test-spa-id --concurrency 6
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import subprocess
import time

import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}
_FLOOR = float(os.getenv("DEBUG_QA_RETRIEVAL_FLOOR", "0.30"))

_FIELD_GUIDE = {
    "question": "Câu hỏi gửi tới bot.",
    "reference_facts": "Keyword/số BẮT BUỘC phải có trong answer (golden must-contain, từ scenario.expect).",
    "answer": "Câu BOT THỰC SỰ trả lời (output RAG).",
    "answer_type": "blocked/cache_hit/... — loại output.",
    "latency_ms": "Thời gian trả lời (ms).",
    "tokens": "prompt/completion/cached + cache_hit_ratio (CR prompt-cache).",
    "intent": "Intent pipeline phát hiện.",
    "decomposed": "Multi-hop: câu có bị tách sub-query không.",
    "cache_status": "hit/miss/bypassed (semantic cache).",
    "n_chunks_used": "Số chunk graded đưa vào LLM.",
    "score_max": "Score chunk cao nhất (chất lượng retrieval).",
    "top_chunks_retrieved": "Chunk bot lấy về + đưa LLM (doc, chunk_index, score, preview) — NGUỒN câu trả lời.",
    "answer_source_chunk": "Chunk trong CORPUS thật chứa đáp án (tìm qua DB). in_retrieved=có vào top-K không; in_corpus=corpus có không.",
    "expect_in_answer": "reference_facts có trong answer không (PASS).",
    "expect_in_retrieved": "reference_facts có trong chunk đưa LLM không.",
    "prelim_verdict": "Verdict RULE-BASED sơ bộ (gợi ý, KHÔNG phải điểm cuối).",
    "fail_step": "Tầng lỗi pin từ evidence (RETRIEVAL/FILTER/GENERATION/HALLU/DATA).",
    "claude_verdict": "[TRỐNG] Agent Claude điền sau khi đọc: ✅CHUẨN/🟡GENERATION/🔴RETRIEVAL/🟠HALLU/⚪DATA.",
    "claude_notes": "[TRỐNG] Agent Claude điền: mạnh/yếu, đúng/thiếu/sai, vì sao.",
}
_VERDICT_LEGEND = {
    "✅ CHUẨN": "chunk retrieved + LLM answered correctly (đủ + đúng)",
    "🟡 GENERATION": "chunk đúng vào LLM nhưng LLM trả thiếu/sai/lược bỏ fact",
    "🔴 RETRIEVAL": "chunk chứa đáp án CÓ trong corpus nhưng KHÔNG vào top-K",
    "🟠 HALLU": "bịa — claim không grounded (trap answered)",
    "⚪ DATA": "corpus KHÔNG có chunk chứa đáp án (golden có thể stale)",
}


def _norm(s: str) -> str:
    return re.sub(r"(?<=\d)[.,\s](?=\d)", "", (s or "").lower())


def _hit(expect: str, text: str) -> bool:
    if not expect:
        return False
    return expect.lower() in text.lower() or _norm(expect) in _norm(text)


_REFUSAL = ("vui lòng liên hệ", "liên hệ hotline", "liên hệ trực tiếp",
            "tham khảo văn bản", "cơ quan có thẩm quyền")
_DENIAL = re.compile(r"(không|chưa)\s+(có|thấy|tìm thấy|quy định|đề cập|bao gồm|thuộc|"
                     r"tồn tại|cung cấp|bán|nằm trong|được\s+(quy định|đề cập|trích dẫn))")


def _refused(a: str) -> bool:
    al = (a or "").lower()
    return bool(_DENIAL.search(al)) or any(m in al for m in _REFUSAL)


def _pg_url() -> str:
    return re.sub(r"postgresql\+asyncpg://", "postgresql://",
                  os.environ.get("DATABASE_URL", ""))


def _corpus_answer_chunk(bot: str, expect: str) -> dict:
    """Find the corpus chunk that actually holds the golden fact (via DB)."""
    if not expect:
        return {"in_corpus": None, "preview": ""}
    # try literal + thousands-separated variants
    pats = {expect, expect.replace(".", ""), re.sub(r"(\d)(?=(\d{3})+$)", r"\1.", expect)}
    like = " OR ".join("c.content ILIKE :p%d" % i for i in range(len(pats)))
    params = {f"p{i}": f"%{p}%" for i, p in enumerate(pats)}
    sql = (
        "SELECT left(c.content, 200) FROM document_chunks c JOIN bots b "
        "ON b.id=c.record_bot_id WHERE b.bot_id=:bot AND (" + like + ") LIMIT 1"
    )
    # psql can't bind named easily here; build a safe-ish inline (corpus is ours)
    where = " OR ".join(f"c.content ILIKE '%{p.replace(chr(39),'')}%'" for p in pats)
    q = (f"SELECT left(c.content,200) FROM document_chunks c JOIN bots b "
         f"ON b.id=c.record_bot_id WHERE b.bot_id='{bot}' AND ({where}) LIMIT 1")
    out = subprocess.run(["psql", _pg_url(), "-tA", "-c", q],
                         capture_output=True, text=True, timeout=20)
    prev = out.stdout.strip()
    return {"in_corpus": bool(prev), "preview": prev[:200]}


async def _token(c):
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


async def _ask(c, tok, bot, ch, ws, q, cid):
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": ws,
            "question": q, "connect_id": cid, "bypass_cache": True, "debug": "full"}
    t0 = time.perf_counter()
    try:
        r = await c.post(f"{BASE}/api/ragbot/test/chat",
                         headers={"Authorization": f"Bearer {tok}", **_BYPASS},
                         json=body, timeout=180)
        d = r.json()
        d["_lat_ms"] = round((time.perf_counter() - t0) * 1000)
        return d
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "_lat_ms": round((time.perf_counter() - t0) * 1000)}


def _record(bot: str, q: dict, resp: dict) -> dict:
    ans = resp.get("answer") or ""
    expect = q.get("expect", "")
    flow = q.get("flow", "")
    is_trap = flow.endswith("_trap")
    dbg = resp.get("debug") or {}
    chunks = resp.get("retrieved_chunks_content") or []
    top = [
        {"doc": (c.get("source") or "?"), "chunk_id": (c.get("chunk_id") or "")[:8],
         "score": round(float(c.get("score") or 0), 4),
         "preview": (c.get("content") or "")[:240].replace("\n", " ")}
        for c in chunks[:8]
    ]
    expect_in_ans = _hit(expect, ans)
    expect_in_ret = bool(expect) and any(_hit(expect, c.get("content", "")) for c in chunks)
    src = _corpus_answer_chunk(bot, expect)
    src["in_retrieved"] = expect_in_ret
    refused = _refused(ans) or resp.get("answer_type") == "blocked"
    score_max = dbg.get("score_max", 0) or 0
    graded = dbg.get("chunks_graded")

    # deterministic prelim verdict + fail_step (evidence-pinned hint)
    if is_trap:
        prelim = "✅ CHUẨN" if refused else "🟠 HALLU"
        fail = "" if refused else "HALLU"
    elif expect_in_ans:
        prelim, fail = "✅ CHUẨN", ""
    elif not expect:
        prelim = "✅ CHUẨN" if (ans and not refused) else "⚪ DATA"
        fail = "" if (ans and not refused) else "REFUSE"
    elif src.get("in_corpus") is False:
        prelim, fail = "⚪ DATA", "DATA"          # golden not in corpus (stale?)
    elif expect_in_ret:
        prelim, fail = "🟡 GENERATION", "GENERATION"  # data reached LLM, answer missed
    elif (dbg.get("top_k") or 0) == 0 or (score_max < _FLOOR and not expect_in_ret):
        prelim, fail = "🔴 RETRIEVAL", "RETRIEVAL"
    elif graded == 0:
        prelim, fail = "🔴 RETRIEVAL", "FILTER"
    else:
        prelim, fail = "🔴 RETRIEVAL", "RETRIEVAL"

    tok = resp.get("tokens") or {}
    return {
        "id": q["id"], "category": flow, "question": q["q"],
        "reference_facts": expect, "is_trap": is_trap,
        "answer": ans, "answer_type": resp.get("answer_type", ""),
        "latency_ms": resp.get("_lat_ms"),
        "tokens": {"prompt": tok.get("prompt"), "completion": tok.get("completion"),
                   "cached": tok.get("cached"),
                   "cache_hit_ratio": dbg.get("cache_hit_ratio")},
        "intent": dbg.get("intent"), "decomposed": dbg.get("query_decomposed"),
        "cache_status": dbg.get("cache_status"),
        "n_chunks_used": graded, "top_k": dbg.get("top_k"),
        "score_max": round(float(score_max), 4),
        "top_chunks_retrieved": top,
        "answer_source_chunk": src,
        "expect_in_answer": expect_in_ans, "expect_in_retrieved": expect_in_ret,
        "prelim_verdict": prelim, "fail_step": fail,
        "claude_verdict": "", "claude_notes": "",
    }


async def _run_bot(c, tok, path, sem):
    sc = json.load(open(path))
    bot, ch, ws = sc["bot_id"], sc["channel_type"], sc.get("workspace_id", "")

    async def _one(q):
        async with sem:
            resp = await _ask(c, tok, bot, ch, ws, q["q"], f"lt-{q['id']}")
            return _record(bot, q, resp)

    recs = await asyncio.gather(*[_one(q) for q in sc["questions"]])
    return bot, ws, list(recs)


async def main(bot_filter, concurrency, stamp):
    files = sorted(glob.glob("tests/scenarios/*_scenario.json"))
    if bot_filter:
        files = [f for f in files if bot_filter in f]
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        results = await asyncio.gather(*[_run_bot(c, tok, f, sem) for f in files])

    for bot, ws, recs in results:
        non = [r for r in recs if not r["is_trap"]]
        ans_q = [r for r in non if r["reference_facts"]]
        pre_pass = [r for r in ans_q if r["prelim_verdict"] == "✅ CHUẨN"]
        traps = [r for r in recs if r["is_trap"]]
        hallu = [r for r in traps if r["prelim_verdict"] == "🟠 HALLU"]
        lat = sorted(r["latency_ms"] for r in recs if r["latency_ms"])
        p95 = lat[int(len(lat) * 0.95)] if lat else 0
        from collections import Counter
        fails = Counter(r["fail_step"] for r in recs if r["fail_step"])
        report = {
            "_description": f"Load-test detail cho bot {bot} — evidence per câu, "
                            "scoring để TRỐNG cho agent Claude đọc chấm.",
            "_field_guide": _FIELD_GUIDE,
            "verdict_legend": _VERDICT_LEGEND,
            "bot": bot, "workspace_id": ws,
            "run": {"stamp": stamp, "n_questions": len(recs),
                    "prelim_coverage": round(len(pre_pass) / len(ans_q), 3) if ans_q else 1.0,
                    "prelim_hallu": f"{len(hallu)}/{len(traps)}",
                    "p95_latency_ms": p95,
                    "fail_step_rollup": dict(fails)},
            "questions": recs,
        }
        path = f"reports/LOADTEST_{bot}_{stamp}.json"
        with open(path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  wrote {path}  ({len(recs)} câu · prelim_cov="
              f"{report['run']['prelim_coverage']} · HALLU={report['run']['prelim_hallu']} "
              f"· p95={p95}ms · fails={dict(fails)})")
    print("\nDONE. Agent Claude: đọc reports/LOADTEST_<bot>_*.json, điền "
          "claude_verdict + claude_notes mỗi câu (KHÔNG ChatGPT).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--stamp", default="run")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.bot, a.concurrency, a.stamp)))
