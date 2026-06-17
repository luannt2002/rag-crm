"""Build ONE master dashboard from all per-bot RAGAS forensic reports.

Consolidates reports/MULTISTEP_RAGAS_<bot>.md into a single document with:
  §1 Executive scorecard  — per-bot faithfulness/correctness + ✅/🟡/🔴/🟠/⚪ counts
  §2 Per-question-type     — which question SHAPE fails most
  §3 Vấn đề theo TẦNG      — every failing question grouped by root layer
                             (RETRIEVAL / GENERATION / HALLU / DATA), with the
                             exact bot+Q+what-is-wrong → a fix roadmap by layer
  §4 Forensic chi tiết     — full per-bot, per-question decision-tree blocks

Output: reports/MULTISTEP_MASTER_DASHBOARD.md
Usage:  PYTHONPATH=. python scripts/ragas_master_dashboard.py
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

REPORTS = Path(__file__).parent.parent / "reports"
OUT = REPORTS / "MULTISTEP_MASTER_DASHBOARD.md"

_HEAD_RE = re.compile(
    r"## Q(\d+) \[([a-z_]+)\]\s+(✅ CHUẨN|🟡 GENERATION|🔴 RETRIEVAL|🟠 HALLU|⚪ DATA/COMPUTED|🟡 MIXED|🟡 LLM-GEN|🔴 RETRIEVAL/HALLU)\s+(?:coverage=(?P<cov>[0-9.]+|n/a)\s+)?faithfulness=([0-9.]+)\s+answer_correctness=([0-9.]+)"
)
_ROOT_RE = re.compile(r"→ \*\*ROOT CAUSE: (.+?)\*\*")
_Q_RE = re.compile(r"\*\*Câu hỏi:\*\* (.+)")
_CORP_RE = re.compile(r"Chunk đúng CÓ trong corpus\? \*\*(.+?)\*\*")
_TOPK_RE = re.compile(r"vào top-K \(retrieved\)\? \*\*(.+?)\*\*")
_LLM_RE = re.compile(r"LLM trả đúng KHI có chunk\? \*\*(.+?)\*\*")
_SHORT_ROOT = {
    "ok": "—",
    "generation": "MODEL+PROMPT (LLM tính sai/bỏ fact) — KHÔNG phải retrieval",
    "retrieval": "CONFIG/LUỒNG (top_k/embedding/rerank) — chunk ngoài top-K",
    "hallu": "MODEL+PROMPT anti-fabricate (bịa, không grounded)",
    "data": "DATA/COMPUTED (corpus thiếu hoặc giá trị tính toán)",
}

_LAYER = {
    "🔴 RETRIEVAL": "retrieval", "🔴 RETRIEVAL/HALLU": "retrieval",
    "🟡 GENERATION": "generation", "🟡 LLM-GEN": "generation", "🟡 MIXED": "generation",
    "🟠 HALLU": "hallu", "⚪ DATA/COMPUTED": "data", "✅ CHUẨN": "ok",
}


def _parse_blocks(text: str) -> list[dict]:
    """Split a report into per-question blocks with parsed metadata."""
    blocks = []
    parts = re.split(r"(?=^## Q\d+ \[)", text, flags=re.M)
    for p in parts:
        m = _HEAD_RE.search(p)
        if not m:
            continue
        root = _ROOT_RE.search(p)
        ques = _Q_RE.search(p)
        corp = _CORP_RE.search(p)
        topk = _TOPK_RE.search(p)
        llm = _LLM_RE.search(p)
        _cov = m.group("cov")
        # NOTE: the optional named (?P<cov>) group is positional group 4, so
        # faithfulness/answer_correctness shift to groups 5/6.
        blocks.append({
            "qn": int(m.group(1)), "type": m.group(2), "verdict": m.group(3),
            "faith": float(m.group(5)), "corr": float(m.group(6)),
            "cov": (float(_cov) if _cov and _cov != "n/a" else None),
            "layer": _LAYER.get(m.group(3), "generation"),
            "root": root.group(1) if root else "",
            "question": (ques.group(1)[:110] if ques else ""),
            "in_corpus": (corp.group(1) if corp else "?"),
            "in_topk": (topk.group(1) if topk else "?"),
            "llm_ok": (llm.group(1) if llm else "?"),
            "body": p.strip(),
        })
    return blocks


def main() -> None:
    bots: dict[str, list[dict]] = {}
    for f in sorted(REPORTS.glob("MULTISTEP_RAGAS_*.md")):
        bot = f.stem.replace("MULTISTEP_RAGAS_", "")
        bots[bot] = _parse_blocks(f.read_text(encoding="utf-8"))
    allq = [(b, q) for b, qs in bots.items() for q in qs]
    if not allq:
        print("No reports."); return

    L = ["# MULTI-STEP MASTER DASHBOARD — RAGAS forensic (12 bot)\n"]
    n = len(allq)
    cnt = defaultdict(int)
    for _, q in allq:
        cnt[q["layer"]] += 1
    af = sum(q["faith"] for _, q in allq) / n
    ac = sum(q["corr"] for _, q in allq) / n
    _covs = [q["cov"] for _, q in allq if q.get("cov") is not None]
    _cov_agg = (sum(_covs) / len(_covs)) if _covs else None
    _cov_txt = f"{_cov_agg:.2f}" if _cov_agg is not None else "n/a"
    L.append(
        f"**Tổng: {n} câu / {len(bots)} bot** · COVERAGE **{_cov_txt}** · faithfulness **{af:.2f}** · answer_correctness **{ac:.2f}**\n\n"
        f"✅ CHUẨN {cnt['ok']} ({100*cnt['ok']//n}%) · "
        f"🟡 GENERATION {cnt['generation']} · 🔴 RETRIEVAL {cnt['retrieval']} · "
        f"🟠 HALLU {cnt['hallu']} · ⚪ DATA {cnt['data']}\n"
    )

    # §0 per-bot decision-tree tables (compact)
    L.append("\n## §0 — BẢNG DECISION-TREE từng câu / từng bot\n")
    _vshort = {"✅ CHUẨN": "✅ CHUẨN", "🟡 GENERATION": "🟡 GEN", "🟡 LLM-GEN": "🟡 GEN",
               "🟡 MIXED": "🟡 MIXED", "🔴 RETRIEVAL": "🔴 RET", "🔴 RETRIEVAL/HALLU": "🔴 RET",
               "🟠 HALLU": "🟠 HALLU", "⚪ DATA/COMPUTED": "⚪ DATA"}
    for bot in sorted(bots):
        qs = sorted(bots[bot], key=lambda x: x["qn"])
        if not qs:
            continue
        af = sum(x["faith"] for x in qs) / len(qs)
        ac = sum(x["corr"] for x in qs) / len(qs)
        L.append(f"\n### 🤖 {bot} — faith {af:.2f} · correct {ac:.2f}\n")
        L.append("| Q | dạng | verdict | corr | chunk trong corpus? | trong top-K? | LLM đúng khi có chunk? | sai ở đâu |")
        L.append("|---|---|---|---|---|---|---|---|")
        for q in qs:
            sai = "—" if q["layer"] == "ok" else _SHORT_ROOT.get(q["layer"], "")
            L.append(f"| Q{q['qn']} | {q['type']} | {_vshort.get(q['verdict'], q['verdict'])} | "
                     f"{q['corr']:.2f} | {q['in_corpus']} | {q['in_topk']} | {q['llm_ok']} | {sai} |")

    # §1 per-bot scorecard
    L.append("\n## §1 — Scorecard từng bot (lĩnh vực)\n")
    L.append("| Bot | COVERAGE | faith | correct | ✅ | 🟡gen | 🔴ret | 🟠hallu | ⚪data |")
    L.append("|---|---|---|---|---|---|---|---|---|")

    def _bot_cov(qs: list[dict]) -> float | None:
        cv = [x["cov"] for x in qs if x.get("cov") is not None]
        return (sum(cv) / len(cv)) if cv else None
    for bot in sorted(bots, key=lambda b: (_bot_cov(bots[b]) if _bot_cov(bots[b]) is not None else 1.0)):
        qs = bots[bot]
        if not qs:
            continue
        c = defaultdict(int)
        for q in qs:
            c[q["layer"]] += 1
        _bc = _bot_cov(qs)
        L.append(f"| {bot} | {(f'{_bc:.2f}' if _bc is not None else 'n/a')} | "
                 f"{sum(x['faith'] for x in qs)/len(qs):.2f} | "
                 f"{sum(x['corr'] for x in qs)/len(qs):.2f} | {c['ok']} | {c['generation']} | "
                 f"{c['retrieval']} | {c['hallu']} | {c['data']} |")

    # §2 per-type
    L.append("\n## §2 — Theo DẠNG câu (shape nào yếu)\n")
    byt: dict[str, list] = defaultdict(list)
    for _, q in allq:
        byt[q["type"]].append(q)
    L.append("| Dạng | n | faith | correct | %CHUẨN |")
    L.append("|---|---|---|---|---|")
    for t in sorted(byt, key=lambda t: sum(x["corr"] for x in byt[t]) / len(byt[t])):
        qs = byt[t]
        pct = 100 * sum(1 for x in qs if x["layer"] == "ok") // len(qs)
        L.append(f"| {t} | {len(qs)} | {sum(x['faith'] for x in qs)/len(qs):.2f} | "
                 f"{sum(x['corr'] for x in qs)/len(qs):.2f} | {pct}% |")

    # §3 vấn đề theo tầng
    L.append("\n## §3 — VẤN ĐỀ theo TẦNG (roadmap fix)\n")
    groups = {
        "retrieval": "🔴 RETRIEVAL — chunk đúng có trong corpus nhưng KHÔNG vào top-K → fix CONFIG/LUỒNG (top_k, embedding, rerank, metadata-filter)",
        "hallu": "🟠 HALLU/FAITHFULNESS — câu trả lời có claim KHÔNG grounded (bịa) → fix MODEL + PROMPT anti-fabricate",
        "generation": "🟡 GENERATION — chunk đúng ĐÃ retrieve nhưng LLM trả sai/thiếu → fix MODEL + PROMPT (KHÔNG phải retrieval)",
        "data": "⚪ DATA/COMPUTED — đáp án không có verbatim trong corpus (computed hoặc corpus thiếu)",
    }
    for layer, title in groups.items():
        items = [(b, q) for b, q in allq if q["layer"] == layer]
        if not items:
            continue
        L.append(f"\n### {title}  ({len(items)} câu)\n")
        for b, q in items:
            L.append(f"- **{b}** Q{q['qn']} [{q['type']}] faith={q['faith']:.2f} corr={q['corr']:.2f} — {q['question']}")

    # §4 forensic chi tiết
    L.append("\n\n## §4 — FORENSIC CHI TIẾT (từng câu từng bot)\n")
    for bot in sorted(bots):
        L.append(f"\n---\n# 🤖 {bot}\n")
        for q in sorted(bots[bot], key=lambda x: x["qn"]):
            L.append(q["body"] + "\n")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"💾 {OUT}  ({n} câu / {len(bots)} bot)")
    # echo §1-§3 to console
    print("\n".join(L[:60]))


if __name__ == "__main__":
    main()
