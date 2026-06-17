"""Render the most-detailed per-bot graded report from GRADED_*.json.

Reads ``reports/GRADED_SUMMARY.json`` (+ per-bot ``reports/GRADED_<bot>.json``)
produced by ``loadtest_graded.py`` and emits a single markdown file with, for
EVERY question: the question text, the bot's actual answer, pass_rate, the
DB ground-truth verdict, the LLM-judge verdict, the HALLU flag, and the
failure-attribution layer.

Run-level ZeroEntropy 429 stats are passed in via ``--ze-429`` (the harness
itself does not correlate reranker 429s per question — they are a run-level
infra signal, so we report the run aggregate honestly rather than fabricate a
per-question count).

Usage:
    python scripts/render_graded_detail.py [--ze-429 N] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPORTS = Path(__file__).parent.parent / "reports"


def _esc(s: str) -> str:
    """Make a cell safe for a markdown table (no raw pipes / newlines)."""
    return str(s).replace("|", "\\|").replace("\n", " ⏎ ").strip()


def _verdict_str(j: dict) -> str:
    if not j:
        return "—"
    if "refused" in j or "fabricated" in j:
        return f"refused={j.get('refused')}, fabricated={j.get('fabricated')}"
    parts = []
    if "answer_correct" in j:
        parts.append(f"correct={j['answer_correct']}")
    fc = j.get("facts_covered") or {}
    if fc:
        ok = sum(1 for v in fc.values() if v)
        parts.append(f"facts={ok}/{len(fc)}")
    return ", ".join(parts) or "—"


def _bot_section(bot: str, results: list[dict]) -> str:
    npass = sum(1 for r in results if r["passed"])
    nflip = sum(1 for r in results if not r.get("deterministic", True))
    nhallu = sum(1 for r in results if r.get("hallu"))
    nrefuse = sum(1 for r in results if r.get("expect_refuse"))
    lines: list[str] = []
    lines.append(f"## {bot}")
    lines.append("")
    lines.append(
        f"**{npass}/{len(results)} pass** · {nflip} flip · "
        f"{nrefuse} refuse-trap · **HALLU={nhallu}**"
    )
    lines.append("")
    for r in sorted(results, key=lambda x: (x.get("level", ""), x.get("id", ""))):
        flag = "✅" if r["passed"] else "❌"
        det = "" if r.get("deterministic", True) else " ⚠FLIP"
        hl = " 🔴HALLU" if r.get("hallu") else ""
        gap = "" if r.get("db_ground_truth", True) else " ⚠TEST-DATA"
        lines.append(
            f"### {flag} `{r['id']}` [{r.get('level','?')}] "
            f"{r.get('pass_rate','?')}{det}{hl}{gap}"
        )
        lines.append("")
        lines.append(f"- **Câu hỏi**: {_esc(r.get('question','—'))}")
        if r.get("expect_refuse"):
            lines.append("- **Loại**: refuse-trap (kỳ vọng bot TỪ CHỐI, không bịa)")
        gold = r.get("gold_facts") or []
        if gold:
            lines.append(f"- **Gold facts (DB)**: {_esc(', '.join(map(str, gold)))}")
        if r.get("expected_compute"):
            lines.append(f"- **Phép tính kỳ vọng**: {_esc(r['expected_compute'])}")
        lines.append(f"- **DB có đáp án**: {r.get('db_ground_truth', '—')}")
        lines.append(f"- **Judge**: {_esc(_verdict_str(r.get('judge') or {}))}")
        lines.append(f"- **Attribution**: {_esc(r.get('layer','—'))}")
        lines.append(f"- **Bot trả lời**: {_esc(r.get('sample_answer','') or '(rỗng)')}")
        if r.get("turns"):
            for t in r["turns"]:
                mark = "✅" if t.get("ok") else "❌"
                lines.append(f"    - {mark} T{t['turn']}: {_esc(t.get('ans',''))}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ze-429", type=int, default=None,
                    help="run-level ZeroEntropy 429 count (infra signal)")
    ap.add_argument("--ze-rrf", type=int, default=None,
                    help="run-level rerank→RRF degrade count")
    ap.add_argument("--out", default=str(REPORTS / "GRADED_DETAIL_REPORT.md"))
    args = ap.parse_args()

    summary = json.loads((REPORTS / "GRADED_SUMMARY.json").read_text(encoding="utf-8"))
    total_pass = sum(g["pass"] for g in summary)
    total_n = sum(g["n"] for g in summary)
    total_flip = sum(g.get("flip", 0) for g in summary)

    # Per-bot detail is in GRADED_<bot>.json (richer than the summary slice).
    out: list[str] = []
    out.append("# Graded Detail Report — trạng thái mới nhất")
    out.append("")
    out.append(f"**GRAND TOTAL: {total_pass}/{total_n} pass · {total_flip} flips**")
    out.append("")
    if args.ze_429 is not None:
        out.append(
            f"**Reranker infra (run-level)**: ZeroEntropy 429 = "
            f"**{args.ze_429}**"
            + (f" · degrade→RRF = {args.ze_rrf}" if args.ze_rrf is not None else "")
        )
        out.append("")
    out.append("| Bot | Pass | Flip | HALLU |")
    out.append("|---|---|---|---|")
    bot_results: dict[str, list[dict]] = {}
    for g in summary:
        bot = g["bot"]
        p = REPORTS / f"GRADED_{bot}.json"
        results = json.loads(p.read_text(encoding="utf-8"))["results"] if p.exists() else g["results"]
        bot_results[bot] = results
        nhallu = sum(1 for r in results if r.get("hallu"))
        out.append(f"| {bot} | {g['pass']}/{g['n']} | {g.get('flip',0)} | {nhallu} |")
    out.append("")
    out.append("---")
    out.append("")
    for bot, results in bot_results.items():
        out.append(_bot_section(bot, results))
        out.append("---")
        out.append("")

    Path(args.out).write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {args.out} ({total_pass}/{total_n} pass, "
          f"{sum(1 for rs in bot_results.values() for r in rs if r.get('hallu'))} HALLU)")


if __name__ == "__main__":
    main()
