"""Tabular → STRUCTURED MARKDOWN (AdapChunk L1 for tables).

A spreadsheet/CSV is NOT "one table with the first row as the header" — a single
sheet routinely stacks MANY sub-tables, each with its own SECTION TITLE and its
own local HEADER (the spa price sheet has chăm-sóc-da / massage / gội-đầu /
triệt-lông tables in one tab). The legacy row-as-chunk parsers took row-1 as the
global header, so a "Mép | 129.000" triệt-lông row got mislabelled with the
chăm-sóc-da title and lost its service context entirely.

This module rebuilds the structure with a small, DOMAIN-NEUTRAL state machine
(shape-based only — no service/brand vocabulary):

  SECTION_TITLE : a single short non-money cell on its own row → ``## <title>``
  HEADER        : a multi-cell row of short label-like cells, no money
                  → opens a markdown table (its columns are the local headers)
  DATA          : a row carrying values/money → a markdown table row
  NOTE          : a single-cell prose / bullet / "Giá: …" line → kept as text
  SEPARATOR     : an all-empty row → closes the current table

Every emitted table is placed UNDER its nearest section title, so the downstream
chunker/extractor can bind each row to BOTH its column header (B2) and its
service/section (B3). One canonical structured form for every tabular source.
"""
from __future__ import annotations

import re

from ragbot.shared.number_format import parse_money_vn

# A cell that is "label-like": short, not a long sentence. Tunable via length
# only (shape, not vocabulary). Headers + section titles are short; prose notes
# and description bullets are long or punctuated.
_MAX_LABEL_CHARS = 40
_BULLET_LEAD = ("-", "•", "*", "–", "—", "●", "·", "▪", "+", "✓", "→")


# A cell that is PURELY a money value — digits + thousands separators + an optional
# VN/EN currency unit, with NO descriptive words. This distinguishes a real PRICE
# ("899000", "1.499.000", "6 triệu") from a package NAME that merely contains a
# number ("Gói 6 triệu") or a column label with a trailing digit ("date1",
# "date2"). The latter are LABELS/NAMES, not prices — they must NOT block header
# detection or be treated as a value. Shape-only, domain-neutral.
_PURE_MONEY_RE = re.compile(
    r"^\s*\d[\d.,\s]*\s*(đ|vnd|k|tr|triệu|trieu|m|nghìn|nghin|ngàn|ngan)?\s*$",
    re.IGNORECASE,
)


def _nonempty(cells: list[str]) -> list[str]:
    return [c.strip() for c in cells if c and c.strip()]


def _is_pure_money(cell: str) -> bool:
    c = cell.strip()
    return bool(c) and _PURE_MONEY_RE.match(c) is not None and parse_money_vn(c) is not None


def _has_money(cells: list[str]) -> bool:
    return any(_is_pure_money(c) for c in cells)


def _is_label_like(cell: str) -> bool:
    c = cell.strip()
    if not c or len(c) > _MAX_LABEL_CHARS:
        return False
    if c[:1] in _BULLET_LEAD:
        return False
    # A label is not a full sentence — reject if it ends a sentence or has many words.
    if c.endswith((".", "!", "?", "…", ":")):
        return False
    if _is_pure_money(c):  # a price VALUE is not a column label
        return False
    return len(c.split()) <= 6  # noqa: PLR2004 — header cells are short phrases


def _looks_header(cells: list[str]) -> bool:
    """A header row: text column-labels, no PRICE value (a priced row is DATA)."""
    ne = _nonempty(cells)
    if len(ne) < 2:  # noqa: PLR2004
        return False
    if any(_is_pure_money(c) for c in ne):
        return False
    # Most non-empty cells are short text labels (column names) — a "name | code |
    # price" header still qualifies even when one label carries a number ("date1").
    return sum(1 for c in ne if _is_label_like(c)) >= max(2, (len(ne) + 1) // 2)


def _md_escape(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ").strip()


def rows_to_structured_markdown(rows: list[list[str]]) -> str:
    """Convert raw spreadsheet rows into section-bound structured markdown."""
    out: list[str] = []
    header: list[str] | None = None
    table_open = False

    def close_table() -> None:
        nonlocal table_open
        if table_open:
            out.append("")  # blank line after a table
            table_open = False

    for raw in rows:
        cells = [(c or "").strip() for c in raw]
        ne = _nonempty(cells)

        # SEPARATOR — close any open table, table boundary.
        if not ne:
            close_table()
            header = None
            continue

        # SECTION_TITLE — exactly one short, non-money, non-bullet cell.
        if len(ne) == 1:
            only = ne[0]
            is_title = (
                len(only) <= _MAX_LABEL_CHARS * 2
                and parse_money_vn(only) is None
                and only[:1] not in _BULLET_LEAD
                and not only.endswith((".", "…"))
                and len(only.split()) <= 8  # noqa: PLR2004
            )
            if is_title:
                close_table()
                header = None
                out.append(f"\n## {only}\n")
            else:
                # NOTE (prose / bullet / "Giá 1 buổi: …") — keep as text.
                close_table()
                out.append(only)
            continue

        # HEADER — opens a new markdown table under the current section.
        if _looks_header(cells) and not _has_money(cells):
            close_table()
            header = [(c or "").strip() or f"col{i + 1}" for i, c in enumerate(cells)]
            # Trim trailing empty header columns.
            while header and not header[-1].strip():
                header.pop()
            cols = [_md_escape(h or f"col{i + 1}") for i, h in enumerate(header)]
            out.append("| " + " | ".join(cols) + " |")
            out.append("| " + " | ".join("---" for _ in cols) + " |")
            table_open = True
            continue

        # DATA row.
        if table_open and header:
            vals = [(cells[i] if i < len(cells) else "") for i in range(len(header))]
            out.append("| " + " | ".join(_md_escape(v) for v in vals) + " |")
        else:
            # Data row with no open header → emit a bare pipe row so structure
            # (and the value↔position) is still preserved for the chunker.
            out.append("| " + " | ".join(_md_escape(c) for c in ne) + " |")
            table_open = False

    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


__all__ = ["rows_to_structured_markdown"]
