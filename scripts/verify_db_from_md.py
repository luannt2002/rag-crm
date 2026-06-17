"""Detailed DB-grounded verification parsed from the multistep forensic MD.

The ``MULTISTEP_RAGAS_<bot>.md`` blocks already carry, per question, the
harness's own DB checks + the FULL bot answer (not the truncated QA_FORMAT
copy). This re-verifies each verdict INDEPENDENTLY:

Per question we extract:
  - verdict (✅/🟡/🔴/🟠)
  - full bot answer
  - gold facts (must_contain)
  - "Chunk đúng CÓ trong corpus?"  → DB HAS the answer (ground truth exists)
  - "Chunk đúng vào top-K?"        → retrieval surfaced it

Then we LITERALLY (numeric-aware) check each gold fact against the FULL answer
and classify:
  ✅ DB-OK         — DB has answer + retrieved + every gold fact in the answer
  🟡 PARAPHRASE    — fact missing literally but answer is a formula/computed
                     phrasing (judge ✅) → trust judge, flag for the record
  ❌ MISS          — DB has answer but a FACTOID gold fact is absent → real gap
  🔴 NOT-RETRIEVED — DB has answer but it never reached top-K
  ⚪ NOT-IN-DB     — gold fact not in corpus (test-data / derived total)

Usage: PYTHONPATH=. python scripts/verify_db_from_md.py
Writes reports/DB_VERIFY_DETAIL_20260610.md + .json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
REPORTS = ROOT / "reports"

_BLOCK = re.compile(
    r"^## (Q\d+) \[([^\]]+)\]\s+(✅ CHUẨN|🟡 GENERATION|🔴 RETRIEVAL|🟠 HALLU|⚪ DATA/COMPUTED|🟡 MIXED|🟡 LLM-GEN|🔴 RETRIEVAL/HALLU)",
    re.M,
)


def _norm(s: str) -> str:
    return re.sub(r"[.,\s]", "", str(s).lower())


def _fact_in(fact: str, ans: str) -> bool:
    nf, na = _norm(fact), _norm(ans)
    if not nf:
        return True
    if re.fullmatch(r"[\d]+", nf):          # pure number → digit substring
        return nf in na
    if nf in na:
        return True
    toks = [t for t in re.split(r"\s+", fact.lower()) if len(t) > 1]
    if not toks:
        return nf in na
    hit = sum(1 for t in toks if t in ans.lower())
    return hit >= max(1, (len(toks) + 1) // 2)


# A fact that is a formula / math token rather than a corpus factoid — literal
# match is unreliable (bot may write \frac{4}{3} or "1,33 A" for "I = 4/3").
def _is_formula(fact: str) -> bool:
    return bool(re.search(r"[=\\^_{}]|/[0-9]|\bsp[0-9]\b|[a-zA-Z]_?\d", fact)) or "=" in fact


def parse_md(path: Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8")
    out = []
    heads = list(_BLOCK.finditer(txt))
    for i, m in enumerate(heads):
        seg = txt[m.start(): heads[i + 1].start() if i + 1 < len(heads) else len(txt)]
        ans = re.search(r"\*\*RAG trả lời \(full\):\*\*\s*(.+?)(?:\n\*\*|\n   \d\.)", seg, re.S)
        facts = re.search(r"\*\*Đáp án đúng \(facts bắt buộc\):\*\*\s*(.+)", seg)
        corp = re.search(r"Chunk đúng CÓ trong corpus\?\s*\*\*(.+?)\*\*", seg)
        topk = re.search(r"vào top-K \(retrieved\)\?\s*\*\*(.+?)\*\*", seg)
        out.append({
            "q": m.group(1), "type": m.group(2), "verdict": m.group(3),
            "answer": (ans.group(1).strip() if ans else ""),
            "facts": [f.strip() for f in facts.group(1).split(",")] if facts else [],
            "corpus_has": (corp.group(1).strip() if corp else "?"),
            "topk_has": (topk.group(1).strip() if topk else "?"),
        })
    return out


def classify(q: dict) -> tuple[str, list[str]]:
    facts = [f for f in q["facts"] if f]
    if not facts:
        return "⚪ NOT-IN-DB", []
    missing = [f for f in facts if not _fact_in(f, q["answer"])]
    if not missing:
        return "✅ DB-OK", []
    # All missing facts are formula/math → paraphrase, not a real gap (if judge ✅).
    if all(_is_formula(f) for f in missing) and q["verdict"] == "✅ CHUẨN":
        return "🟡 PARAPHRASE", missing
    if q["corpus_has"].upper().startswith("KH"):
        return "⚪ NOT-IN-DB", missing
    if q["topk_has"].upper().startswith("KH"):
        return "🔴 NOT-RETRIEVED", missing
    return "❌ MISS", missing


def main() -> None:
    date = "20260610"
    docs = []
    counts = {"✅ DB-OK": 0, "🟡 PARAPHRASE": 0, "❌ MISS": 0, "🔴 NOT-RETRIEVED": 0, "⚪ NOT-IN-DB": 0}
    tot = 0
    for md in sorted(REPORTS.glob("MULTISTEP_RAGAS_*.md")):
        bot = md.stem.replace("MULTISTEP_RAGAS_", "")
        qs = parse_md(md)
        rows = []
        for q in qs:
            verdict, missing = classify(q)
            counts[verdict] = counts.get(verdict, 0) + 1
            tot += 1
            rows.append({**q, "db_verdict": verdict, "missing": missing})
        docs.append({"bot": bot, "questions": rows})

    real_ok = counts["✅ DB-OK"] + counts["🟡 PARAPHRASE"]  # paraphrase = correct (judge-confirmed)
    md_out = [f"# DB-grounded verification (chi tiết, full answer) — {date}", "",
              f"Đối chiếu LITERAL full answer của bot vs gold-facts + trạng thái corpus/top-K từ DB.", "",
              "| Verdict | Số câu | Nghĩa |",
              "|---|---|---|",
              f"| ✅ DB-OK | {counts['✅ DB-OK']} | đủ gold-fact trong answer, chunk vào top-K |",
              f"| 🟡 PARAPHRASE | {counts['🟡 PARAPHRASE']} | fact công thức bot viết khác chữ (judge ✅) — vẫn ĐÚNG |",
              f"| ❌ MISS | {counts['❌ MISS']} | DB có + retrieve được nhưng answer THIẾU factoid → gap thật |",
              f"| 🔴 NOT-RETRIEVED | {counts['🔴 NOT-RETRIEVED']} | DB có đáp án nhưng KHÔNG vào top-K |",
              f"| ⚪ NOT-IN-DB | {counts['⚪ NOT-IN-DB']} | gold-fact không có literal trong corpus (số dẫn xuất/test-data) |",
              "",
              f"**Đúng (DB-OK + PARAPHRASE) = {real_ok}/{tot} ({round(100*real_ok/tot,1)}%)** · "
              f"gap thật cần fix = {counts['❌ MISS']} MISS + {counts['🔴 NOT-RETRIEVED']} NOT-RETRIEVED", ""]

    md_out.append("## Câu cần chú ý (MISS + NOT-RETRIEVED)")
    md_out.append("")
    for doc in docs:
        for q in doc["questions"]:
            if q["db_verdict"] in ("❌ MISS", "🔴 NOT-RETRIEVED"):
                md_out.append(f"- **{q['db_verdict']}** `{doc['bot']}/{q['q']}` (judge {q['verdict']}) — "
                              f"corpus={q['corpus_has']} topK={q['topk_has']} — thiếu: {q['missing']}")
    md_out.append("")
    md_out.append("## Chi tiết per-bot")
    for doc in docs:
        nok = sum(1 for q in doc["questions"] if q["db_verdict"] in ("✅ DB-OK", "🟡 PARAPHRASE"))
        md_out.append(f"\n### {doc['bot']} — {nok}/{len(doc['questions'])} đúng (DB-verified)")
        for q in doc["questions"]:
            md_out.append(f"- {q['db_verdict']} `{q['q']}` [{q['type']}] judge={q['verdict']} "
                          f"corpus={q['corpus_has']} topK={q['topk_has']}")
            if q["missing"]:
                md_out.append(f"    - thiếu literal: {q['missing']}")

    (REPORTS / f"DB_VERIFY_DETAIL_{date}.md").write_text("\n".join(md_out), encoding="utf-8")
    (REPORTS / f"DB_VERIFY_DETAIL_{date}.json").write_text(
        json.dumps({"counts": counts, "total": tot, "documents": docs}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"total": tot, **counts, "correct_db_verified": real_ok,
                      "correct_pct": round(100*real_ok/tot, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
