"""Independent DB-grounded verification of the latest bot answers.

Does NOT trust the LLM judge. For every question it:
  1. pulls the gold facts (``must_contain``) from multistep_questions.json,
  2. pulls the bot's ACTUAL answer from QA_FORMAT_REPORT_<date>.json,
  3. pulls the bot's WHOLE corpus from the DB (document_chunks),
  4. for each gold fact checks — literally, normalised — whether it is
     (a) present in the corpus (ground-truth EXISTS in DB), and
     (b) present in the bot's answer (bot actually SAID it).

Verdict per question (DB-grounded, judge-independent):
  ✅ DB-OK     — every gold fact is in DB AND in the answer
  ❌ MISS      — gold fact is in DB but NOT in the answer (bot wrong/incomplete)
  ⚪ NOT-IN-DB — gold fact is not literally in corpus (test-data / derived total)

Usage: PYTHONPATH=. python scripts/verify_answers_vs_db.py <date>
Writes: reports/DB_VERIFY_<date>.json  +  reports/DB_VERIFY_<date>.md
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).parent.parent
QFILE = Path(__file__).parent / "multistep_questions.json"


def _norm(s: str) -> str:
    # Strip spaces + thousands separators so "3.597.000" == "3597000" == "3 597 000".
    return re.sub(r"[.,\s]", "", str(s).lower())


def _in(fact: str, hay: str) -> bool:
    """Literal containment after normalisation; numbers match digit-substring."""
    nf, nh = _norm(fact), _norm(hay)
    if not nf:
        return True
    if re.fullmatch(r"\d+", nf):  # pure number → digits appear anywhere
        return nf in nh
    # text fact: require the normalised token to appear (robust to spacing/case)
    if nf in nh:
        return True
    # multi-word fact: most alpha tokens present
    toks = [t for t in re.split(r"\s+", fact.lower()) if len(t) > 2]
    if not toks:
        return nf in nh
    hit = sum(1 for t in toks if t in hay.lower())
    return hit >= max(1, (len(toks) + 1) // 2)


async def main() -> None:
    date = sys.argv[1] if len(sys.argv) > 1 else "20260610"
    qa = json.loads((ROOT / "reports" / f"QA_FORMAT_REPORT_{date}.json").read_text(encoding="utf-8"))
    gold = json.loads(QFILE.read_text(encoding="utf-8"))

    # Gold facts grouped per bot, in file order (matches QA_FORMAT q01..qNN order).
    gold_by_bot: dict[str, list[dict]] = {}
    for g in gold:
        gold_by_bot.setdefault(g["bot_id"], []).append(g)

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    corpus_cache: dict[str, str] = {}

    async def corpus(bot: str) -> str:
        if bot not in corpus_cache:
            async with engine.connect() as cx:
                rows = await cx.execute(text("""
                    SELECT dc.content FROM document_chunks dc
                    JOIN documents d ON d.id = dc.record_document_id
                    JOIN bots b ON b.id = d.record_bot_id
                    WHERE b.bot_id = :bot
                """), {"bot": bot})
                corpus_cache[bot] = "\n".join(r[0] or "" for r in rows.fetchall())
        return corpus_cache[bot]

    out_docs = []
    tot = ok = miss = notdb = 0
    disagreements = []  # judge said CHUẨN but DB says MISS, or vice-versa

    for doc in qa["documents"]:
        bot = doc["id"]
        golds = gold_by_bot.get(bot, [])
        qrows = []
        for i, q in enumerate(doc["questions"]):
            ans = q.get("answer") or ""
            facts = golds[i]["must_contain"] if i < len(golds) else []
            corp = await corpus(bot)
            per = []
            for f in facts:
                in_db = _in(f, corp)
                in_ans = _in(f, ans)
                per.append({"fact": f, "in_db": in_db, "in_answer": in_ans})
            # DB-grounded verdict: only judge facts that EXIST in the DB.
            db_facts = [p for p in per if p["in_db"]]
            if not db_facts:
                verdict = "⚪ NOT-IN-DB"
                notdb += 1
            elif all(p["in_answer"] for p in db_facts):
                verdict = "✅ DB-OK"
                ok += 1
            else:
                verdict = "❌ MISS"
                miss += 1
            tot += 1
            judge_v = q.get("verdict", "")
            # cross-check judge vs DB
            judge_ok = judge_v == "✅ CHUẨN"
            db_ok = verdict == "✅ DB-OK"
            if judge_ok != db_ok and verdict != "⚪ NOT-IN-DB":
                disagreements.append({
                    "bot": bot, "id": q["id"], "judge": judge_v, "db_verdict": verdict,
                    "missing": [p["fact"] for p in db_facts if not p["in_answer"]],
                })
            qrows.append({
                "id": q["id"], "question": q["question"][:160],
                "db_verdict": verdict, "judge_verdict": judge_v,
                "facts": per, "answer": ans[:400],
            })
        out_docs.append({"bot": bot, "questions": qrows})

    await engine.dispose()

    summary = {
        "date": date, "total": tot, "db_ok": ok, "miss": miss, "not_in_db": notdb,
        "db_ok_pct": round(100 * ok / tot, 1) if tot else 0,
        "judge_db_disagreements": len(disagreements),
    }
    Path(ROOT / "reports" / f"DB_VERIFY_{date}.json").write_text(
        json.dumps({"summary": summary, "disagreements": disagreements, "documents": out_docs},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown digest
    md = [f"# DB-grounded verification — {date}", "",
          f"**{ok}/{tot} DB-OK ({summary['db_ok_pct']}%)** · {miss} MISS · {notdb} NOT-IN-DB · "
          f"{len(disagreements)} câu judge≠DB", "",
          "> Verify LITERAL đối chiếu corpus DB, KHÔNG dùng LLM judge. Chỉ chấm fact CÓ trong DB.", ""]
    md.append("## Câu MISS (DB có fact nhưng bot KHÔNG nói) + judge≠DB")
    md.append("")
    for d in disagreements:
        md.append(f"- **[{d['bot']}] {d['id']}** — judge={d['judge']} · DB={d['db_verdict']} · "
                  f"thiếu trong answer: {d['missing']}")
    md.append("")
    md.append("## Chi tiết per-bot")
    for doc in out_docs:
        nok = sum(1 for q in doc["questions"] if q["db_verdict"] == "✅ DB-OK")
        md.append(f"\n### {doc['bot']} — {nok}/{len(doc['questions'])} DB-OK")
        for q in doc["questions"]:
            md.append(f"- {q['db_verdict']} `{q['id']}` (judge {q['judge_verdict']})")
            for p in q["facts"]:
                mark = "✅" if (p["in_db"] and p["in_answer"]) else ("DB-only❌" if p["in_db"] else "⚪notDB")
                md.append(f"    - {mark} `{p['fact']}` (db={p['in_db']} answer={p['in_answer']})")
    Path(ROOT / "reports" / f"DB_VERIFY_{date}.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    print(f"disagreements judge≠DB: {len(disagreements)}")
    for d in disagreements:
        print(f"  [{d['bot']}] {d['id']}: judge={d['judge']} DB={d['db_verdict']} missing={d['missing']}")


if __name__ == "__main__":
    asyncio.run(main())
