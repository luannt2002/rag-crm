"""Parallel hard-query load test across ALL bots (bypass_cache, fact-grounded).

Fires every question in multistep_questions_extra.json (the HARD set: 6/bot ×
12 bots) concurrently with a semaphore (feedback_ragas_parallel — N=8), each
with bypass_cache=True. Correctness is judged DETERMINISTICALLY by must_contain
fact coverage (substring, digit-normalised), NOT the RAGAS LLM-judge (which we
found unreliable — high score ≠ correct). This is a fast Coverage proxy:

  PASS    = answered AND every required fact present
  PARTIAL = answered AND some (not all) facts present
  MISS    = answered AND no required fact present
  BLOCKED = answer_type == blocked (guardrail / refuse)
  ERROR   = transport/HTTP error

Usage: python scripts/loadtest_hard_parallel.py
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import os

import httpx

BASE = "http://localhost:3004/api/ragbot/test"
QFILE = Path(__file__).parent / "multistep_questions_extra.json"
SEM = asyncio.Semaphore(int(os.getenv("LOADTEST_CONCURRENCY", "8")))


def _norm(s: str) -> str:
    # normalise digits: strip thousands separators so "199.000" == "199000"
    return re.sub(r"(?<=\d)[.\s](?=\d{3}\b)", "", s.lower())


def _coverage(answer: str, musts: list[str]) -> tuple[int, int]:
    a = _norm(answer)
    hit = sum(1 for m in musts if _norm(str(m)) in a)
    return hit, len(musts)


async def _ask(c: httpx.AsyncClient, bot: str, q: str) -> dict:
    async with SEM:
        try:
            tok = (await c.get(f"{BASE}/tokens/self", timeout=10)).json()["token"]
            r = await c.post(
                f"{BASE}/chat",
                json={"bot_id": bot, "channel_type": "web", "question": q,
                      "bypass_cache": True},
                headers={"Authorization": f"Bearer {tok}"},
                timeout=90,
            )
            d = r.json()
            p = d.get("data") if isinstance(d, dict) and "data" in d else d
            return p if isinstance(p, dict) else {"_error": f"HTTP{r.status_code}_no_data"}
        except Exception as e:  # noqa: BLE001 — test harness
            return {"_error": f"{type(e).__name__}: {str(e)[:50]}"}


def _verdict(resp: dict, musts: list[str]) -> tuple[str, str]:
    if resp.get("_error"):
        return "ERROR", resp["_error"]
    if resp.get("answer_type") == "blocked":
        return "BLOCKED", ""
    ans = resp.get("answer", "") or ""
    if not musts:
        return ("PASS" if ans else "MISS"), ""
    hit, tot = _coverage(ans, musts)
    if hit == tot:
        return "PASS", f"{hit}/{tot}"
    if hit > 0:
        return "PARTIAL", f"{hit}/{tot}"
    return "MISS", f"0/{tot}"


async def main() -> None:
    qs = json.loads(QFILE.read_text(encoding="utf-8"))
    t0 = time.time()
    async with httpx.AsyncClient() as c:
        resps = await asyncio.gather(*[_ask(c, q["bot_id"], q["question"]) for q in qs])
    per_bot: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows = []
    for q, resp in zip(qs, resps):
        v, detail = _verdict(resp, q.get("must_contain") or [])
        per_bot[q["bot_id"]][v] += 1
        rows.append((q["bot_id"], q["type"], v, detail,
                     (resp.get("answer", "") or "")[:0]))
    elapsed = round(time.time() - t0, 1)

    print(f"=== HARD LOAD TEST (72Q · 12 bot · bypass_cache · {elapsed}s) ===\n")
    order = ["PASS", "PARTIAL", "MISS", "BLOCKED", "ERROR"]
    tot = defaultdict(int)
    print(f"{'bot':<26} PASS PART MISS BLK ERR  %PASS")
    for bot in sorted(per_bot):
        d = per_bot[bot]
        n = sum(d.values())
        for k in order:
            tot[k] += d[k]
        pct = round(100 * d["PASS"] / n) if n else 0
        print(f"{bot:<26} {d['PASS']:>4} {d['PARTIAL']:>4} {d['MISS']:>4} "
              f"{d['BLOCKED']:>3} {d['ERROR']:>3}  {pct:>3}%")
    N = sum(tot.values())
    print(f"\n{'TOTAL':<26} {tot['PASS']:>4} {tot['PARTIAL']:>4} {tot['MISS']:>4} "
          f"{tot['BLOCKED']:>3} {tot['ERROR']:>3}  {round(100*tot['PASS']/N)}%")
    print(f"\nPASS={tot['PASS']}/{N} ({round(100*tot['PASS']/N)}%) · "
          f"PARTIAL={tot['PARTIAL']} · MISS={tot['MISS']} · "
          f"BLOCKED={tot['BLOCKED']} · ERROR={tot['ERROR']}")


if __name__ == "__main__":
    asyncio.run(main())
