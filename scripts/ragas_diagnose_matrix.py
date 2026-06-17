"""Aggregate per-bot RAGAS reports into a diagnostic matrix.

Reads reports/MULTISTEP_RAGAS_<bot>.md (produced by multistep_ragas_report.py),
extracts per-question (bot, type, faithfulness, answer_correctness), and builds:

  1. Per-bot scorecard (which DOMAIN is strong / weak).
  2. Per-question-TYPE breakdown (which question SHAPE fails most).
  3. Root-cause classification per failing question via the faith×correct quadrant:
       correct ≥ 0.7                      → ✅ CHUẨN
       faith ≥ 0.7 & correct < 0.7        → 🟡 LLM-GEN  (chunk đúng, answer sai: arithmetic/completeness)
       faith < 0.7 & correct < 0.7        → 🔴 RETRIEVAL/HALLU (chunk sai → answer sai/bịa)
       faith < 0.7 & correct ≥ 0.7        → ⚠️  PARAMETRIC (đúng nhưng không grounded — faithfulness risk)

This is the multi-step debug map: it says WHICH bot/domain, WHICH question type,
is/ isn't correct, and WHICH layer (retrieval vs LLM-gen vs faithfulness) to fix.

Usage: PYTHONPATH=. python scripts/ragas_diagnose_matrix.py
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

REPORTS = Path(__file__).parent.parent / "reports"
GOOD = 0.7

_Q_RE = re.compile(
    r"## Q(\d+) \[([a-z_]+)\]\s+faithfulness=([0-9.]+)\s+answer_correctness=([0-9.]+)"
)


def _classify(faith: float, corr: float) -> tuple[str, str]:
    if corr >= GOOD:
        return ("✅ CHUẨN", "ok")
    if faith >= GOOD:
        return ("🟡 LLM-GEN", "llm")       # chunk grounded but answer wrong/incomplete
    if corr < GOOD:
        return ("🔴 RETRIEVAL/HALLU", "ret")  # ungrounded + wrong
    return ("⚠️ PARAMETRIC", "param")


def main() -> None:
    rows = []  # (bot, qn, qtype, faith, corr, tag, cat)
    for f in sorted(REPORTS.glob("MULTISTEP_RAGAS_*.md")):
        bot = f.stem.replace("MULTISTEP_RAGAS_", "")
        for m in _Q_RE.finditer(f.read_text(encoding="utf-8")):
            qn, qtype, fa, co = int(m.group(1)), m.group(2), float(m.group(3)), float(m.group(4))
            tag, cat = _classify(fa, co)
            rows.append((bot, qn, qtype, fa, co, tag, cat))

    if not rows:
        print("No reports yet."); return

    # 1. Per-bot scorecard
    by_bot: dict[str, list] = defaultdict(list)
    for r in rows:
        by_bot[r[0]].append(r)
    print("=" * 72)
    print("1. PER-BOT SCORECARD (domain strength)")
    print("=" * 72)
    print(f"{'bot':28s} {'faith':>6} {'corr':>6} {'✅':>3} {'🟡':>3} {'🔴':>3} {'⚠':>3}")
    for bot in sorted(by_bot):
        rs = by_bot[bot]
        af = sum(r[3] for r in rs) / len(rs)
        ac = sum(r[4] for r in rs) / len(rs)
        c = {k: sum(1 for r in rs if r[6] == k) for k in ("ok", "llm", "ret", "param")}
        print(f"{bot:28s} {af:6.2f} {ac:6.2f} {c['ok']:3d} {c['llm']:3d} {c['ret']:3d} {c['param']:3d}")

    # 2. Per-question-type breakdown
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r[2]].append(r)
    print("\n" + "=" * 72)
    print("2. PER-QUESTION-TYPE (which shape fails most)")
    print("=" * 72)
    print(f"{'type':14s} {'n':>3} {'faith':>6} {'corr':>6} {'%CHUẨN':>8}")
    for qt in sorted(by_type, key=lambda t: sum(x[4] for x in by_type[t]) / len(by_type[t])):
        rs = by_type[qt]
        af = sum(r[3] for r in rs) / len(rs)
        ac = sum(r[4] for r in rs) / len(rs)
        pct = 100 * sum(1 for r in rs if r[6] == "ok") / len(rs)
        print(f"{qt:14s} {len(rs):3d} {af:6.2f} {ac:6.2f} {pct:7.0f}%")

    # 3. Failing questions with root-cause tag
    print("\n" + "=" * 72)
    print("3. CHƯA CHUẨN — câu fail + tầng cần fix (root-cause)")
    print("=" * 72)
    fails = [r for r in rows if r[6] != "ok"]
    for cat, label in (("ret", "🔴 RETRIEVAL/HALLU — fix tầng retrieval / faithfulness"),
                       ("llm", "🟡 LLM-GEN — chunk đúng, LLM sai (arithmetic/completeness)"),
                       ("param", "⚠️ PARAMETRIC — đúng nhưng không grounded")):
        grp = [r for r in fails if r[6] == cat]
        if not grp:
            continue
        print(f"\n{label}  ({len(grp)} câu):")
        for bot, qn, qtype, fa, co, tag, _ in grp:
            print(f"   {bot:26s} Q{qn} [{qtype:11s}] faith={fa:.2f} corr={co:.2f}")

    # 4. Overall
    n = len(rows)
    ok = sum(1 for r in rows if r[6] == "ok")
    print("\n" + "=" * 72)
    print(f"OVERALL: {n} câu · ✅CHUẨN {ok} ({100*ok/n:.0f}%) · "
          f"🟡LLM {sum(1 for r in rows if r[6]=='llm')} · "
          f"🔴RET {sum(1 for r in rows if r[6]=='ret')} · "
          f"⚠PARAM {sum(1 for r in rows if r[6]=='param')}")
    af = sum(r[3] for r in rows) / n
    ac = sum(r[4] for r in rows) / n
    print(f"         faithfulness={af:.2f}  answer_correctness={ac:.2f}  (bots={len(by_bot)})")


if __name__ == "__main__":
    main()
