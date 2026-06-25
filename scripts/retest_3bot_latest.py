"""Re-test the 3 demo bots against the LATEST code (post-restart QC).

Reuses the corpus-derived QA question sets captured in the prior
``reports/LOADTEST_<bot>_20260623b.json`` files, plus explicit bug cases, and
replays them through the synchronous ``/api/ragbot/test/chat`` endpoint in
PARALLEL (asyncio.gather + bounded semaphore — load tests must be parallel).

Deterministic scoring (no LLM judge, reproducible):
  * trap question  → must REFUSE  (else HALLU_BREACH — sacred).
  * normal question→ ANSWERED + every ``expect_in_answer`` token present → PASS;
                     answered but a token missing → PARTIAL; refused → REFUSE_GAP.
Outputs per-bot pass / HALLU / coverage + the full record JSON.

    set -a && source .env && set +a
    python scripts/retest_3bot_latest.py
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import time
from typing import Any

import httpx

from ragbot.shared.constants import (
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
CONCURRENCY = 8

# Explicit bug / regression cases layered on top of the corpus-derived sets.
EXTRA_CASES: dict[str, list[dict[str, Any]]] = {
    "chinh-sach-xe": [
        {
            "id": "bug-xe-265-50R20",
            "question": "Giá lốp 265/50R20 bao nhiêu?",
            "expect_in_answer": ["265/50"],
            "is_trap": False,
            "category": "bug-variant-coverage",
        },
    ],
}


def _bypass_headers() -> dict[str, str]:
    tok = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    return {RAGBOT_LOADTEST_BYPASS_HEADER: tok} if tok else {}


def is_refuse(answer: str) -> bool:
    """Heuristic refuse detector (mirrors the harness)."""
    if not answer:
        return True
    low = answer.lower()
    markers = (
        "xin lỗi", "không tìm thấy", "không có thông tin", "chưa có thông tin",
        "không thể trả lời", "tôi không", "mình không có", "không rõ",
        "no information", "i don't have", "cannot find",
    )
    return any(m in low for m in markers)


async def mint_token(client: httpx.AsyncClient, bot_id: str, channel: str) -> str:
    last = ""
    for attempt in range(4):  # retry — concurrent mints can transiently fail
        try:
            r = await client.get(
                f"{BASE_URL}/api/ragbot/test/tokens/self",
                params={"bot_id": bot_id, "channel_type": channel},
                headers=_bypass_headers(),
                timeout=20,
            )
            r.raise_for_status()
            d = r.json()
            tok = d.get("token") or d.get("data", {}).get("token", "")
            if tok:
                return tok
            last = f"empty token: {str(d)[:120]}"
        except Exception as exc:  # noqa: BLE001 — retry harness mint
            last = str(exc)
        await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"token mint failed for {bot_id}: {last}")


async def ask(
    client: httpx.AsyncClient, token: str, *, bot_id: str, ws: str,
    channel: str, question: str, qid: str,
) -> dict[str, Any]:
    body = {
        "bot_id": bot_id, "channel_type": channel, "workspace_id": ws,
        "question": question, "connect_id": f"retest-{qid}-{int(time.time())}",
        "bypass_cache": True,
    }
    headers = {"Authorization": f"Bearer {token}", **_bypass_headers()}
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/api/ragbot/test/chat", headers=headers, json=body,
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
    except Exception as exc:  # noqa: BLE001 — eval harness: record, never abort
        return {"error": str(exc), "latency_ms": round((time.perf_counter() - t0) * 1000)}
    d["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    return d


def score(q: dict[str, Any], answer: str) -> str:
    if not answer:
        return "ERR"
    refused = is_refuse(answer)
    if q.get("is_trap"):
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    if refused:
        return "REFUSE_GAP"
    expect = q.get("expect_in_answer")
    if not isinstance(expect, (list, tuple)):
        expect = []  # bool / None / str in legacy records → no token gate, answered=PASS
    low = answer.lower()
    missing = [e for e in expect if str(e).lower() not in low]
    return "PASS_ANSWERED" if not missing else "PARTIAL"


def load_questions() -> dict[str, dict[str, Any]]:
    """bot_id -> {ws, channel, questions[]} from the prior result files + extras."""
    bots: dict[str, dict[str, Any]] = {}
    for f in sorted(glob.glob("reports/LOADTEST_*_20260623b.json")):
        d = json.load(open(f))
        bot = d["bot"]
        bots[bot] = {
            "ws": d.get("workspace_id") or "default",
            "channel": "web",
            "questions": list(d.get("questions", [])),
        }
    for bot, extra in EXTRA_CASES.items():
        if bot in bots:
            bots[bot]["questions"].extend(extra)
    return bots


async def run_bot(bot_id: str, cfg: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        token = await mint_token(client, bot_id, cfg["channel"])

        async def _one(q: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                resp = await ask(
                    client, token, bot_id=bot_id, ws=cfg["ws"], channel=cfg["channel"],
                    question=q["question"], qid=str(q.get("id", "?")),
                )
            answer = resp.get("answer", "") or ""
            verdict = score(q, answer)
            return {
                "id": q.get("id"), "category": q.get("category"),
                "question": q["question"], "is_trap": bool(q.get("is_trap")),
                "verdict": verdict, "answer": answer[:400],
                "n_chunks_used": resp.get("n_chunks_used"),
                "score_max": resp.get("score_max"),
                "latency_ms": resp.get("latency_ms"),
                "expect_in_answer": q.get("expect_in_answer"),
            }

        recs = await asyncio.gather(*[_one(q) for q in cfg["questions"]])
    return {"bot": bot_id, "ws": cfg["ws"], "records": recs}


async def main() -> None:
    bots = load_questions()
    sem = asyncio.Semaphore(CONCURRENCY)
    # Run bots SEQUENTIALLY (parallel per-question within a bot) so the 3 token
    # mints don't contend — concurrent mints were failing instantly (10-90ms).
    results = []
    for b, c in bots.items():
        results.append(await run_bot(b, c, sem))

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = f"reports/RETEST_3BOT_LATEST_{ts}.json"
    print(f"\n{'='*70}\nRE-TEST 3 BOT — latest code @ {ts}\n{'='*70}")
    grand = {"PASS_ANSWERED": 0, "PARTIAL": 0, "PASS_REFUSED": 0, "REFUSE_GAP": 0,
             "HALLU_BREACH": 0, "ERR": 0}
    for r in results:
        tally: dict[str, int] = {}
        for rec in r["records"]:
            tally[rec["verdict"]] = tally.get(rec["verdict"], 0) + 1
            grand[rec["verdict"]] = grand.get(rec["verdict"], 0) + 1
        n = len(r["records"])
        ans_pass = tally.get("PASS_ANSWERED", 0)
        print(f"\n🤖 {r['bot']} (ws={r['ws']}) — {n} Q")
        print(f"   {tally}")
        print(f"   answered-PASS rate: {round(ans_pass/max(n,1)*100,1)}% "
              f"| HALLU_BREACH: {tally.get('HALLU_BREACH',0)}")
        for rec in r["records"]:
            flag = "" if rec["verdict"].startswith("PASS") else "  ⚠"
            print(f"     [{rec['verdict']:14s}] {rec['question'][:55]:55s} "
                  f"chunks={rec['n_chunks_used']} {flag}")
    print(f"\n{'='*70}\nGRAND: {grand}")
    print(f"HALLU_BREACH total = {grand['HALLU_BREACH']} (sacred target 0)")
    json.dump(results, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"saved → {out}")


if __name__ == "__main__":
    asyncio.run(main())
