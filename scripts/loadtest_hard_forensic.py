"""Per-question forensic for the HARD load test — layer attribution per fact.

For every hard question (multistep_questions_extra.json, all bots), captures the
answer + retrieved chunks, computes must_contain fact coverage, and for EACH
missing fact attributes the failure layer DETERMINISTICALLY:

  - fact present in answer            → OK
  - missing, but IN a retrieved chunk → 🟡 GENERATION (drop-fact: chunk had it,
                                          LLM dropped it)
  - missing, not retrieved, IN corpus → 🔴 RETRIEVAL (chunk exists, missed top-K)
  - missing, not in corpus            → ⚪ DATA gap

Evidence-only (rule #0): prints verbatim answer + the exact missing fact + where
it was found. No RAGAS LLM-judge. bypass_cache=True. Parallel (semaphore 8).

Output: reports/HARD_FORENSIC_<date-stamped-by-caller>.md
Usage: python scripts/loadtest_hard_forensic.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://localhost:3004/api/ragbot/test"
QFILE = Path(__file__).parent / "multistep_questions_extra.json"
OUT = Path(__file__).parent.parent / "reports" / "HARD_FORENSIC.md"
SEM = asyncio.Semaphore(8)


def _norm(s: str) -> str:
    return re.sub(r"(?<=\d)[.\s](?=\d{3}\b)", "", str(s).lower())


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
            return p if isinstance(p, dict) else {"_error": "no_data"}
        except Exception as e:  # noqa: BLE001
            return {"_error": f"{type(e).__name__}"}


async def _fact_in_corpus(eng, bot: str, fact: str) -> bool:
    async with eng.connect() as conn:
        row = await conn.execute(text("""
            SELECT 1 FROM document_chunks dc JOIN documents d ON d.id = dc.record_document_id
            JOIN bots b ON b.id = d.record_bot_id
            WHERE b.bot_id = :bot AND replace(replace(lower(dc.content),'.',''),' ','')
                  LIKE '%'||:f||'%' LIMIT 1
        """), {"bot": bot, "f": _norm(fact).replace(" ", "")})
        return row.first() is not None


async def main() -> None:
    qs = json.loads(QFILE.read_text(encoding="utf-8"))
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with httpx.AsyncClient() as c:
        resps = await asyncio.gather(*[_ask(c, q["bot_id"], q["question"]) for q in qs])

    lines: list[str] = ["# HARD TEST FORENSIC — per-fact layer attribution\n"]
    layer_tot: dict[str, int] = defaultdict(int)
    per_bot: dict[str, list[str]] = defaultdict(list)

    for q, resp in zip(qs, resps):
        bot = q["bot_id"]
        musts = [str(m) for m in (q.get("must_contain") or [])]
        ans = (resp.get("answer", "") or "")
        atype = resp.get("answer_type", "")
        sources = resp.get("sources") or []
        chunk_blob = _norm(" ".join(
            (s.get("preview", "") or "") + " " + (s.get("content", "") or "")
            for s in sources
        ))
        a_norm = _norm(ans)

        if resp.get("_error") or atype == "blocked":
            verdict = "BLOCKED" if atype == "blocked" else "ERROR"
            per_bot[bot].append(
                f"- **[{q['type']}] {verdict}** — {q['question'][:90]}…\n"
                f"  - ans: {ans[:120] or resp.get('_error')}")
            layer_tot[verdict] += 1
            continue

        missing = [m for m in musts if _norm(m) not in a_norm]
        if not missing:
            layer_tot["OK"] += 1
            continue

        # attribute each missing fact
        attrib = []
        for m in missing:
            if _norm(m).replace(" ", "") in chunk_blob.replace(" ", ""):
                attrib.append((m, "🟡GEN"))
                layer_tot["GEN"] += 1
            elif await _fact_in_corpus(eng, bot, m):
                attrib.append((m, "🔴RET"))
                layer_tot["RET"] += 1
            else:
                attrib.append((m, "⚪DATA"))
                layer_tot["DATA"] += 1
        tag = "PARTIAL" if len(missing) < len(musts) else "MISS"
        per_bot[bot].append(
            f"- **[{q['type']}] {tag}** ({len(musts)-len(missing)}/{len(musts)} facts) "
            f"— {q['question'][:90]}…\n"
            f"  - missing: " + "; ".join(f"`{m}`→{lyr}" for m, lyr in attrib) + "\n"
            f"  - ans: {ans[:160]}")

    await eng.dispose()

    lines.append(
        f"**Layer totals:** 🟡GEN(drop-fact) {layer_tot['GEN']} · "
        f"🔴RET(retrieval miss) {layer_tot['RET']} · ⚪DATA {layer_tot['DATA']} · "
        f"BLOCKED {layer_tot['BLOCKED']} · ERROR {layer_tot['ERROR']} · "
        f"OK(full) {layer_tot['OK']}\n")
    for bot in sorted(per_bot):
        if per_bot[bot]:
            lines.append(f"\n## {bot}\n")
            lines.extend(per_bot[bot])

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:1]))
    print(lines[1])
    print(f"\nWritten: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
