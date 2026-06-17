"""Deep-debug the ANSWER flow per question — diagnose the FAILURE LAYER.

For every scenario question it calls the live ``/chat`` endpoint with
``debug=full`` (which returns ``retrieval_debug`` + ``retrieved_chunks_content``)
and classifies the outcome AND, for every miss, *which layer* failed — so a
wrong/empty answer is never reported as a flat "fail" without a cause:

  * RETRIEVAL_ZERO    — 0 chunks retrieved (nothing matched at all)
  * RETRIEVAL_MISS    — top score below floor (chunking/embedding didn't surface
                        a relevant chunk → it never had a chance to reach top-K)
  * CRAG_REJECT       — chunks retrieved above floor but the grader dropped all
                        (chunks_graded == 0 → grader too strict)
  * WRONG_CHUNK       — chunks reached the LLM but none contain the expected
                        answer (retrieved the wrong rows → chunking/topK issue)
  * LLM_IGNORED_DATA  — the expected answer IS in a chunk that reached the LLM,
                        but the answer omits it (LLM-layer fault, not retrieval)
  * (PASS / PASS_REFUSE / HALLU_BREACH for the happy + trap paths)

This is the "vì sao chưa trả lời được — LLM đã có data hay chunk chưa vào hay
chunking chưa qua top-K?" question, answered with evidence per turn.

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/debug_qa_layers.py
    .venv/bin/python scripts/debug_qa_layers.py --bot test-spa-id --concurrency 8
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re

import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}

# Retrieval floor: a top score below this means embedding/chunking failed to
# surface anything relevant (matches the project's filter_min_score intent).
_RETRIEVAL_FLOOR = float(os.getenv("DEBUG_QA_RETRIEVAL_FLOOR", "0.30"))

_REFUSAL_MARKERS = (
    "vui lòng liên hệ", "liên hệ hotline", "liên hệ trực tiếp",
    "tham khảo văn bản", "cơ quan có thẩm quyền",
)
_DENIAL_RE = re.compile(
    r"(không|chưa)\s+"
    r"(có|thấy|tìm thấy|quy định|đề cập|bao gồm|thuộc|tồn tại|cung cấp|bán|nằm trong|"
    r"đề\s*cập|được\s+(quy định|đề cập|trích dẫn))"
)


def _is_refusal(ans: str) -> bool:
    a = (ans or "").lower()
    return bool(_DENIAL_RE.search(a)) or any(m in a for m in _REFUSAL_MARKERS)


def _norm_num(s: str) -> str:
    return re.sub(r"(?<=\d)[.,\s](?=\d)", "", (s or "").lower())


def _hit(expect: str, text: str) -> bool:
    return expect.lower() in text.lower() or _norm_num(expect) in _norm_num(text)


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
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _diagnose(q: dict, resp: dict) -> dict:
    """Score the turn AND pin the failure layer for misses."""
    ans = resp.get("answer") or ""
    flow = q.get("flow", "")
    is_trap = flow.endswith("_trap")
    refused = _is_refusal(ans) or (resp.get("answer_type") == "blocked")
    expect = q.get("expect")
    dbg = resp.get("debug") or {}
    chunks = resp.get("retrieved_chunks_content") or []
    top_k = dbg.get("top_k", len(chunks))
    graded = dbg.get("chunks_graded", None)
    score_max = dbg.get("score_max", 0) or 0
    expect_in_chunks = bool(expect) and any(_hit(expect, c.get("content", "")) for c in chunks)

    # Happy/trap verdicts first.
    if is_trap:
        verdict = "PASS_REFUSE" if refused else "HALLU_BREACH"
        layer = "" if refused else "TRAP_ANSWERED"
    elif expect:
        if _hit(expect, ans):
            verdict, layer = "PASS", ""
        else:
            verdict = "REFUSE_GAP" if refused else "WRONG"
            # failure-layer diagnosis
            if top_k == 0:
                layer = "RETRIEVAL_ZERO"
            elif score_max < _RETRIEVAL_FLOOR and not expect_in_chunks:
                layer = "RETRIEVAL_MISS"           # chunking/embed didn't surface it
            elif graded == 0 and not expect_in_chunks:
                layer = "CRAG_REJECT"              # grader dropped everything
            elif expect_in_chunks:
                layer = "LLM_IGNORED_DATA"         # data reached LLM, answer omits it
            else:
                layer = "WRONG_CHUNK"              # retrieved, but not the answer row
    else:
        verdict = "ANSWERED" if (ans and not refused) else "REFUSE"
        layer = "" if verdict == "ANSWERED" else "NO_EXPECT_REFUSE"

    return {
        "id": q["id"], "flow": flow, "verdict": verdict, "layer": layer,
        "expect": expect, "top_k": top_k, "graded": graded,
        "score_max": round(float(score_max), 3), "expect_in_chunks": expect_in_chunks,
        "answer": ans[:90].replace("\n", " "),
    }


async def _run_bot(c, tok, path, sem) -> tuple[str, list[dict]]:
    sc = json.load(open(path))
    bot, ch = sc["bot_id"], sc["channel_type"]
    ws = sc.get("workspace_id", "")

    async def _one(q):
        async with sem:
            resp = await _ask(c, tok, bot, ch, ws, q["q"], f"dbg-{q['id']}")
            return _diagnose(q, resp)

    results = await asyncio.gather(*[_one(q) for q in sc["questions"]])
    return bot, results


def _print_bot(bot: str, rs: list[dict]) -> None:
    traps = [r for r in rs if r["flow"].endswith("_trap")]
    non = [r for r in rs if not r["flow"].endswith("_trap")]
    answerable = [r for r in non if r["expect"]]
    passed = [r for r in answerable if r["verdict"] == "PASS"]
    hallu = [r for r in traps if r["verdict"] == "HALLU_BREACH"]
    cov = len(passed) / len(answerable) if answerable else 1.0
    print(f"\n{'='*82}\n### {bot}   coverage={cov:.0%} ({len(passed)}/{len(answerable)})  "
          f"HALLU={len(hallu)}/{len(traps)}  ({len(rs)} câu)\n{'='*82}")
    print(f"  {'id':<16}{'flow':<20}{'verdict':<13}{'layer':<18}{'topK':>5}{'grd':>4}{'sMax':>7}")
    for r in rs:
        flag = "✅" if r["verdict"] in ("PASS", "PASS_REFUSE", "ANSWERED") else "❌"
        print(f"  {flag}{r['id']:<14}{r['flow']:<20}{r['verdict']:<13}{r['layer']:<18}"
              f"{str(r['top_k']):>5}{str(r['graded']):>4}{r['score_max']:>7}")
    # failure-layer histogram
    fails = [r for r in rs if r["verdict"] in ("WRONG", "REFUSE_GAP", "HALLU_BREACH")]
    if fails:
        from collections import Counter
        h = Counter(r["layer"] for r in fails)
        print(f"  → FAILURE LAYERS: {dict(h)}")
        for r in fails:
            print(f"     ! {r['id']} ({r['flow']}): {r['verdict']}/{r['layer']} "
                  f"expect={r['expect']!r} in_chunks={r['expect_in_chunks']} "
                  f"sMax={r['score_max']} → ans={r['answer']!r}")


async def main(bot_filter: str, concurrency: int) -> int:
    files = sorted(glob.glob("tests/scenarios/*_scenario.json"))
    if bot_filter:
        files = [f for f in files if bot_filter in f]
    sem = asyncio.Semaphore(concurrency)
    rc = 0
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        all_rs = await asyncio.gather(*[_run_bot(c, tok, f, sem) for f in files])
    print("ANSWER-FLOW DEEP DEBUG — verdict + failure-layer per question")
    print(f"(retrieval floor={_RETRIEVAL_FLOOR} · debug=full · bypass_cache)")
    for bot, rs in all_rs:
        _print_bot(bot, rs)
        if any(r["verdict"] == "HALLU_BREACH" for r in rs):
            rc = 1
    # global layer rollup
    from collections import Counter
    g = Counter()
    for _, rs in all_rs:
        for r in rs:
            if r["verdict"] in ("WRONG", "REFUSE_GAP", "HALLU_BREACH"):
                g[r["layer"]] += 1
    print(f"\n{'='*82}\nGLOBAL FAILURE-LAYER ROLLUP: {dict(g) or 'none — all passed'}")
    print("  RETRIEVAL_MISS/ZERO=chunking·embed | CRAG_REJECT=grader | "
          "WRONG_CHUNK=topK | LLM_IGNORED_DATA=LLM had data, didn't use")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="")
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.bot, a.concurrency)))
