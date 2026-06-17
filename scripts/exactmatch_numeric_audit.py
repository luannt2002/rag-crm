"""Action #0 — DETERMINISTIC numeric correctness audit (no LLM judge).

The gpt-4.1 judge is blind to one-digit numeric errors (11.990.000 scored
faithful vs corpus 11.999.000). So Faithfulness/AnswerRelevancy do NOT certify
price/fine/legal-code correctness. This checks it deterministically.

For the high-stakes factoid bots (prices, traffic fines, legal codes) it:
  1. asks each question (N runs → catch flaky numbers),
  2. extracts every number / money / code from the bot's answer,
  3. classifies each against the corpus (exact, normalised):
       GROUNDED      — appears verbatim in the corpus,
       COMPUTED      — = a sum of corpus numbers (bot did arithmetic),
       FABRICATED    — not in corpus and not a sum of corpus numbers,
  4. flags COMPUTED+FABRICATED as the numbers the LLM judge would miss.

Usage: PYTHONPATH=. python scripts/exactmatch_numeric_audit.py [--runs 3] [--bots ...]
Writes reports/EXACTMATCH_AUDIT_20260611.json
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import re
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).parent.parent
QFILE = Path(__file__).parent / "multistep_questions.json"
BASE = "http://localhost:3004/api/ragbot/test"
HIGH_STAKES = ["test-spa-id", "luat-giao-thong", "thong-tu-09-2020-tt-nhnn"]

# money "1.199.000" / "11999000" / plain ints ≥ 1000, and legal codes "18/2018".
_NUM = re.compile(r"\b\d{1,3}(?:\.\d{3})+\b|\b\d{4,}\b|\b\d{1,3}/\d{4}\b")


def _norm(s: str) -> str:
    return re.sub(r"[.,\s]", "", str(s).lower())


def _nums(text_: str) -> list[str]:
    return _NUM.findall(text_ or "")


def _as_int(tok: str) -> int | None:
    d = re.sub(r"[.\s]", "", tok)
    return int(d) if d.isdigit() else None


async def _token(c):
    r = await c.get(f"{BASE}/tokens/self", timeout=10)
    return r.json()["token"]


async def _ask(c, bot, q):
    for attempt in range(5):
        t = await _token(c)
        r = await c.post(f"{BASE}/chat",
                         json={"bot_id": bot, "channel_type": "web", "question": q, "bypass_cache": True},
                         headers={"Authorization": f"Bearer {t}"}, timeout=120)
        if r.status_code == 503:
            await asyncio.sleep(4 * (attempt + 1)); continue
        if r.status_code != 200:
            return None
        d = r.json(); d = d.get("data", d)
        return d.get("answer", "")
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--bots", nargs="*", default=HIGH_STAKES)
    args = ap.parse_args()

    gold = json.loads(QFILE.read_text(encoding="utf-8"))
    by_bot: dict[str, list[dict]] = {}
    for g in gold:
        if g["bot_id"] in args.bots:
            by_bot.setdefault(g["bot_id"], []).append(g)

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    corpus: dict[str, str] = {}
    corpus_norm: dict[str, str] = {}
    corpus_sums: dict[str, set[int]] = {}   # precomputed pairwise sums+diffs (once)

    async def _corpus(bot):
        if bot not in corpus:
            async with engine.connect() as cx:
                rows = await cx.execute(text("""
                    SELECT dc.content FROM document_chunks dc
                    JOIN documents d ON d.id=dc.record_document_id
                    JOIN bots b ON b.id=d.record_bot_id WHERE b.bot_id=:b"""), {"b": bot})
                corpus[bot] = "\n".join(r[0] or "" for r in rows.fetchall())
            corpus_norm[bot] = _norm(corpus[bot])
            ints = sorted({i for i in (_as_int(t) for t in _nums(corpus[bot])) if i})[:300]
            sums = set()
            for a, b in itertools.combinations(ints, 2):
                sums.add(a + b); sums.add(abs(a - b))
            corpus_sums[bot] = sums
        return corpus[bot]

    def _classify(num: str, bot: str) -> str:
        if _norm(num) in corpus_norm[bot]:
            return "GROUNDED"
        v = _as_int(num)
        if v is None:
            return "FABRICATED"
        if v in corpus_sums[bot]:           # O(1) lookup
            return "COMPUTED"
        return "FABRICATED"

    out_docs = []
    async with httpx.AsyncClient() as c:
        for bot, golds in by_bot.items():
            await _corpus(bot)
            rows = []
            for g in golds:
                runs = []
                for _ in range(args.runs):
                    a = await _ask(c, bot, g["question"])
                    runs.append(a or "")
                # use the last non-empty answer for number extraction; flag flaky
                ans = next((r for r in runs if r), "")
                nums = _nums(ans)
                classified = [{"num": x, "verdict": _classify(x, bot)} for x in nums]
                bad = [x for x in classified if x["verdict"] != "GROUNDED"]
                # flaky if the set of numbers differs across runs
                numsets = [tuple(sorted(set(_nums(r)))) for r in runs if r]
                flaky = len(set(numsets)) > 1
                rows.append({
                    "id": g.get("id") or g["question"][:40],
                    "question": g["question"][:120],
                    "answer": ans[:300],
                    "gold": g.get("must_contain", []),
                    "numbers": classified,
                    "ungrounded": bad,
                    "flaky_numbers": flaky,
                    "empty_runs": sum(1 for r in runs if not r),
                })
            n_bad = sum(1 for r in rows if r["ungrounded"])
            n_flaky = sum(1 for r in rows if r["flaky_numbers"])
            out_docs.append({"bot": bot, "n": len(rows),
                             "questions_with_ungrounded_number": n_bad,
                             "questions_flaky": n_flaky, "questions": rows})
            print(f"  {bot:24s} ungrounded-number: {n_bad}/{len(rows)} · flaky: {n_flaky}", flush=True)

    await engine.dispose()
    (ROOT / "reports" / "EXACTMATCH_AUDIT_20260611.json").write_text(
        json.dumps({"runs": args.runs, "documents": out_docs}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("wrote reports/EXACTMATCH_AUDIT_20260611.json", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
