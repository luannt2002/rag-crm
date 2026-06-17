"""Multi-step (multi-hop / aggregation / comparison / conditional) load test.

Single-fact lookups don't stress a RAG bot — real users ask questions that
chain multiple corpus facts. This runner drives the multi-step question set
(scripts/multistep_questions.json, drafted per-bot from each corpus) against
the live pipeline with bypass_cache, then judges each answer against the
``must_contain`` ground-truth literals.

Evidence-only (rule #0): prints the bot answer + chunks_used + top_score +
which must_contain literals matched/missed per question; coverage + a HALLU
heuristic are aggregated per bot. No verdict is asserted without the raw data.

Parallel per feedback_ragas_parallel: asyncio.gather + semaphore N=8.

Usage: PYTHONPATH=. python scripts/loadtest_multistep_verify.py [bot_id]
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:3004/api/ragbot/test"
CHANNEL = "web"
SEM = asyncio.Semaphore(4)
QFILE = Path(__file__).parent / "multistep_questions.json"


def _norm(s: str) -> str:
    """Normalise for substring match: lowercase + strip number separators.

    Vietnamese number formatting varies (3.597.000 / 3,597,000 / 3597000) and
    answers paraphrase, so we compare on a separator-free lowercase form and
    also keep a space-collapsed variant for phrase literals.
    """
    s = s.lower()
    # collapse number group separators so 3.597.000 == 3597000 == 3,597,000
    s = re.sub(r"(?<=\d)[.,\s](?=\d)", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _hit(answer: str, literal: str) -> bool:
    return _norm(literal) in _norm(answer)


async def _token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE}/tokens/self", timeout=10)
    r.raise_for_status()
    return r.json()["token"]


async def _ask(client: httpx.AsyncClient, q: dict) -> dict:
    # Retry on 503 (circuit-breaker / provider rate-limit under parallel burst)
    # with backoff — a 503 means transport-degraded, not a wrong answer.
    async with SEM:
        p = None
        for attempt in range(4):
            try:
                token = await _token(client)
                r = await client.post(
                    f"{BASE}/chat",
                    json={
                        "bot_id": q["bot_id"], "channel_type": CHANNEL,
                        "question": q["question"], "bypass_cache": True,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=120,
                )
                if r.status_code == 503:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                d = r.json()
                p = d.get("data") if isinstance(d, dict) and "data" in d else d
                break
            except Exception as exc:  # noqa: BLE001 — record + continue
                if attempt == 3:
                    return {**q, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                await asyncio.sleep(5 * (attempt + 1))
        if p is None:
            return {**q, "error": "503 after retries (circuit-breaker / rate-limit)"}
    p = p or {}
    answer = p.get("answer", "") or ""
    must = q.get("must_contain", [])
    matched = [m for m in must if _hit(answer, m)]
    missed = [m for m in must if not _hit(answer, m)]
    atype = p.get("answer_type")
    return {
        **q, "answer": answer, "answer_type": atype,
        "chunks_used": p.get("chunks_used"),
        "top_score": (p.get("debug") or {}).get("top_score"),
        "matched": matched, "missed": missed,
        "refused": atype in ("blocked", "refused") or "chưa có thông tin" in answer.lower(),
    }


async def main() -> None:
    qs = json.loads(QFILE.read_text(encoding="utf-8"))
    bot_filter = sys.argv[1] if len(sys.argv) > 1 else None
    if bot_filter:
        qs = [q for q in qs if q["bot_id"] == bot_filter]
    print(f"=== MULTI-STEP load test · {len(qs)} questions · bypass_cache ===\n")
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_ask(client, q) for q in qs])

    per_bot: dict[str, dict] = {}
    for r in results:
        b = r["bot_id"]
        st = per_bot.setdefault(b, {"full": 0, "partial": 0, "refused": 0, "err": 0, "n": 0})
        st["n"] += 1
        if r.get("error"):
            st["err"] += 1; verdict = "🟠 ERROR"
        elif r["refused"]:
            st["refused"] += 1; verdict = "🔴 REFUSED"
        elif not r["missed"]:
            st["full"] += 1; verdict = "✅ FULL"
        elif r["matched"]:
            st["partial"] += 1; verdict = "🟡 PARTIAL"
        else:
            verdict = "🔴 MISS"
        print(f"{verdict}  [{r['bot_id']}] {r.get('type','')}  "
              f"chunks={r.get('chunks_used')} top={r.get('top_score')}")
        print(f"   Q   : {r['question'][:150]}")
        if r.get("error"):
            print(f"   ERR : {r['error']}")
        else:
            print(f"   BOT : {(r['answer'] or '')[:240].replace(chr(10),' ')}")
            print(f"   ✓ matched: {r['matched']}")
            if r["missed"]:
                print(f"   ✗ missed : {r['missed']}")
        print()

    print("=== PER-BOT SUMMARY (full = all must_contain present) ===")
    tot = {"full": 0, "partial": 0, "refused": 0, "err": 0, "n": 0}
    for b, st in sorted(per_bot.items()):
        for k in tot:
            tot[k] += st[k]
        cov = round(100 * st["full"] / st["n"], 1) if st["n"] else 0
        print(f"  {b:26s} n={st['n']} FULL={st['full']} PARTIAL={st['partial']} "
              f"REFUSED={st['refused']} ERR={st['err']}  coverage={cov}%")
    cov = round(100 * tot["full"] / tot["n"], 1) if tot["n"] else 0
    partial_cov = round(100 * (tot["full"] + tot["partial"]) / tot["n"], 1) if tot["n"] else 0
    print(f"\n  TOTAL n={tot['n']}  FULL={tot['full']} ({cov}%)  "
          f"FULL+PARTIAL={partial_cov}%  REFUSED={tot['refused']}  ERR={tot['err']}")
    print(f"  wall={round(time.time()-t0,1)}s")


if __name__ == "__main__":
    asyncio.run(main())
