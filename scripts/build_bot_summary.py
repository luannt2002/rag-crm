"""Build a per-bot SUMMARY + FULL-LISTING document — solves the classic RAG "list
everything" limitation.

A query like "liệt kê tất cả dịch vụ" / "có những sản phẩm gì" cannot be answered by
top-K retrieval (K rows ≠ all rows; raising K floods the LLM context). The fix is a
pre-aggregated document: ONE doc that lists every entity grouped by category, plus an
overview. A "list/summary" query then retrieves this single comprehensive chunk.

Built deterministically from the stats index (parse_table_chunks → name/price/category)
over the bot's happy-case files — NO LLM, domain-neutral. Headings are phrased as the
stock questions ("Có những dịch vụ gì", "Liệt kê", "Tóm tắt") so retrieval matches them.

    python scripts/build_bot_summary.py            # reads reports/happy_case_clone/
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ragbot.shared.chunking.analyze import _is_heading_line  # noqa: E402
from ragbot.shared.document_stats import parse_table_chunks  # noqa: E402
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402

CLONE = Path(__file__).resolve().parent.parent / "reports" / "happy_case_clone"


def format_vnd(v: int) -> str:
    """700000 → '700.000đ' (Vietnamese thousands separator)."""
    return f"{v:,}".replace(",", ".") + "đ"

# bot → its source files (the happy-case styled corpus)
BOTS = {
    "spa": ["spa-1.csv", "spa-2.csv", "spa-3.csv", "spa-4.md"],
    "xe": ["xe-1.csv", "xe-2.csv", "xe-3.csv", "xe-4.md"],
    "legal": ["thongtu-09-2020.csv"],
}


def _entities_of(files: list[str]):
    """All priced entities across a bot's sheet files (deduped by name)."""
    seen: dict[str, tuple[str, int | None, str | None]] = {}
    for fn in files:
        p = CLONE / fn
        if not p.exists() or p.suffix != ".csv":
            continue
        text = p.read_text(encoding="utf-8")
        # Skip DOC-like files (prose/legal with markdown headings) — running the
        # catalog extractor on legal sentences mis-reads numbers as "prices". A doc
        # bot's summary is a table-of-contents (handled by _headings_of), not a list.
        if sum(1 for ln in text.splitlines() if ln.lstrip().startswith("#")) >= 5:  # noqa: PLR2004
            continue
        md = rows_to_structured_markdown(list(csv.reader(io.StringIO(text))))
        for e in parse_table_chunks([{"content": md}]):
            if e.name and e.name not in seen:
                seen[e.name] = (e.name, e.price_primary, e.category)
    return list(seen.values())


def _headings_of(files: list[str]) -> list[str]:
    """Section headings across a bot's doc files (for prose/legal bots)."""
    out: list[str] = []
    for fn in files:
        p = CLONE / fn
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("#") and _is_heading_line(s):
                title = s.lstrip("#").strip()
                if title and title not in out:
                    out.append(title)
    return out


def build_summary(bot: str, files: list[str]) -> str:
    ents = _entities_of(files)
    headings = _headings_of(files)
    lines: list[str] = [f"# Tóm tắt & danh sách đầy đủ — bot {bot}\n"]

    if ents:
        prices = [p for _, p, _ in ents if p]
        cats = sorted({c for _, _, c in ents if c})
        # ── Overview (matches "tóm tắt", "có những dịch vụ gì") ──
        lines.append("## Có những dịch vụ / sản phẩm gì? (tóm tắt)\n")
        ov = f"Tổng cộng **{len(ents)}** dịch vụ/sản phẩm"
        if cats:
            ov += f" thuộc **{len(cats)}** nhóm: {', '.join(cats)}"
        if prices:
            ov += f". Giá từ {format_vnd(min(prices))} đến {format_vnd(max(prices))}"
        lines.append(ov + ".\n")
        # ── Full listing grouped by category (matches "liệt kê tất cả") ──
        lines.append("## Liệt kê chi tiết tất cả dịch vụ / sản phẩm\n")
        by_cat: dict[str, list[tuple[str, int | None]]] = {}
        for name, price, cat in ents:
            by_cat.setdefault(cat or "Khác", []).append((name, price))
        for cat in sorted(by_cat):
            lines.append(f"\n### {cat}\n")
            for name, price in by_cat[cat]:
                lines.append(f"- {name}" + (f" — {format_vnd(price)}" if price else ""))

    if headings:
        lines.append("\n## Mục lục / các phần trong tài liệu\n")
        for h in headings[:80]:  # noqa: PLR2004 — cap a runaway TOC
            lines.append(f"- {h}")

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    if not CLONE.exists():
        print(f"{CLONE} not found — run normalize_to_happy_case.py first")
        sys.exit(2)
    print("Building per-bot summary + full-listing docs:\n")
    for bot, files in BOTS.items():
        summary = build_summary(bot, files)
        out = CLONE / f"{bot}-00-summary.md"
        out.write_text(summary, encoding="utf-8")
        n_lines = summary.count("\n- ")
        print(f"  {bot:6} → {out.name}  ({len(summary)} chars, ~{n_lines} mục liệt kê)")
        # preview first lines
        for ln in summary.splitlines()[:6]:
            print(f"        {ln}")
        print()


if __name__ == "__main__":
    main()
