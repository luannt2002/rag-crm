#!/usr/bin/env python3
"""Vietnamese Information Retrieval recall@k eval — Paper 25 pattern.

Read a load-test JSON aggregate and compute recall@k (k=1,3,5,7) for
Vietnamese-language queries only. A turn is considered a "hit" at depth k
if the answer is grounded (answer_type=answered or at least 1 chunk used).

Usage:
    python scripts/eval_vn_recall.py \\
        --input reports/LOADTEST_90Q_FULLMINI_1778018956.json \\
        --output reports/VN_RECALL_EVAL.md \\
        [--label V14]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Quick VN detection: at least one Vietnamese-specific character or tone mark
_VN_CHARS_RE = re.compile(
    r"[àáảãạâầấẩẫậăằắẳẵặèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)

# Common Vietnamese function words for stronger detection
_VN_WORDS_RE = re.compile(
    r"\b(của|và|một|những|trong|được|không|cho|có|với|tại|các|này|đó|để|về|ra|vào|khi|nếu|thì|là|mà|như|nên|chỉ|còn|hay|hoặc|vì|sao)\b",
    re.IGNORECASE,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def is_vietnamese(text: str) -> bool:
    """Heuristic VN detection: tone marks + common words."""
    if _VN_CHARS_RE.search(text):
        return True
    words = text.lower().split()
    # At least 3 common VN words
    vn_word_count = len(_VN_WORDS_RE.findall(text))
    return vn_word_count >= 3


def compute_recall_at_k(
    turns: list[dict[str, Any]],
    *,
    k: int,
    label: str = "",
) -> dict[str, Any]:
    """Simple recall@k: a turn is a 'hit' if chunks_used > 0."""
    total = len(turns)
    if total == 0:
        return {"k": k, "total": 0, "hits": 0, "recall": 0.0, "label": label}

    hits = sum(1 for t in turns if int(t.get("chunks_used", 0)) > 0)
    # For multi-hop / synthesis turns, require chunks >= k
    strict_hits = sum(1 for t in turns if int(t.get("chunks_used", 0)) >= k)

    return {
        "k": k,
        "total": total,
        "hits": hits,
        "strict_hits": strict_hits,
        "recall": hits / total,
        "strict_recall": strict_hits / total,
        "label": label,
    }


def chunk_distribution(turns: list[dict[str, Any]]) -> dict[int, int]:
    dist: dict[int, int] = defaultdict(int)
    for t in turns:
        c = int(t.get("chunks_used", 0))
        dist[c] += 1
    return dict(sorted(dist.items()))


def render_report(
    label: str,
    vn_turns: list[dict[str, Any]],
    ks: list[int],
    section_breakdown: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [
        f"# VN Recall@k Eval — {label}",
        "",
        f"> Total VN turns: {len(vn_turns)} | Sections: {len(section_breakdown)}",
        "",
        "## Overall Recall@k",
        "",
        "| k | Hits (≥1 chunk) | Recall | Strict Hits (≥k) | Strict Recall |",
        "|---|---|---|---|---|",
    ]
    for k in ks:
        r = compute_recall_at_k(vn_turns, k=k)
        lines.append(
            f"| {k} | {r['hits']} | {r['recall']:.3f} | "
            f"{r['strict_hits']} | {r['strict_recall']:.3f} |"
        )

    lines += [
        "",
        "## Per-section Recall@k",
        "",
        "| Section | N | Recall@1 | Recall@3 | Recall@5 | Recall@7 |",
        "|---|---|---|---|---|---|",
    ]
    for sec, turns in sorted(section_breakdown.items()):
        parts = [sec, str(len(turns))]
        for k in ks:
            r = compute_recall_at_k(turns, k=k)
            parts.append(f"{r['recall']:.3f}")
        lines.append("| " + " | ".join(parts) + " |")

    lines += [
        "",
        "## Chunk Distribution",
        "",
        "| Chunks Used | Count | % |",
        "|---|---|---|",
    ]
    dist = chunk_distribution(vn_turns)
    total = len(vn_turns)
    for c, count in dist.items():
        pct = count / total * 100 if total else 0
        lines.append(f"| {c} | {count} | {pct:.1f}% |")

    lines += [
        "",
        "## Top Score Distribution",
        "",
        "| Range | Count |",
        "|---|---|",
    ]
    ranges: dict[str, int] = defaultdict(int)
    for t in vn_turns:
        s = float(t.get("top_score", 0))
        if s == 0:
            rng = "0.000 (no chunks)"
        elif s < 0.05:
            rng = "0.001-0.049"
        elif s < 0.10:
            rng = "0.050-0.099"
        elif s < 0.20:
            rng = "0.100-0.199"
        elif s < 0.40:
            rng = "0.200-0.399"
        else:
            rng = "0.400+"
        ranges[rng] += 1
    for rng, count in sorted(ranges.items()):
        lines.append(f"| {rng} | {count} |")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="VN IR recall@k eval — Paper 25")
    parser.add_argument("--input", required=True, help="Path to loadtest JSON")
    parser.add_argument("--output", default="reports/VN_RECALL_EVAL.md")
    parser.add_argument("--label", default="V14")
    parser.add_argument("--ks", default="1,3,5,7", help="Comma-separated k values")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input not found: {input_path}")
        sys.exit(1)

    data = load_json(input_path)
    results = data.get("results", data.get("turns", []))
    ks = [int(k.strip()) for k in args.ks.split(",") if k.strip()]

    vn_turns = [r for r in results if is_vietnamese(r.get("question", ""))]
    non_vn = len(results) - len(vn_turns)

    print(f"Total turns: {len(results)} | VN: {len(vn_turns)} | Non-VN: {non_vn}")

    if not vn_turns:
        print("No Vietnamese turns found.")
        sys.exit(1)

    # Section breakdown
    section_breakdown: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in vn_turns:
        sec = t.get("section", "unknown")
        section_breakdown[sec].append(t)

    report = render_report(args.label, vn_turns, ks, section_breakdown)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Report written: {out_path}")


if __name__ == "__main__":
    main()
