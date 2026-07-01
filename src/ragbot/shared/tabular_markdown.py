"""Tabular → STRUCTURED MARKDOWN (AdapChunk L1 for tables).

A spreadsheet/CSV is NOT "one table with the first row as the header" — a single
sheet routinely stacks MANY sub-tables, each with its own SECTION TITLE and its
own local HEADER (e.g. a price sheet with several service groups in one tab). The
legacy row-as-chunk parsers took row-1 as the global header, so a data row under a
later section got mislabelled with the first section's title and lost its context.

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

from ragbot.shared.constants import (
    DEFAULT_TABLE_GAP_ROWS,
    DEFAULT_TABLE_LABEL_MAX_CHARS,
)
from ragbot.shared.number_format import parse_money_vn

# A cell that is "label-like": short, not a long sentence. Tunable via length
# only (shape, not vocabulary). Headers + section titles are short; prose notes
# and description bullets are long or punctuated.
_MAX_LABEL_CHARS = DEFAULT_TABLE_LABEL_MAX_CHARS
_BULLET_LEAD = ("-", "•", "*", "–", "—", "●", "·", "▪", "+", "✓", "→")  # noqa: RUF001 — real corpus bullet chars


# Currency-unit tokens (VN + EN shorthand), LONGEST first so "triệu" is consumed
# before the "tr" shorthand. Used to strip the money skeleton from a cell so any
# LEFTOVER letters reveal a descriptive word (→ a NAME, not a price).
_MONEY_UNIT_RE = re.compile(
    r"(triệu|trieu|nghìn|nghin|ngàn|ngan|vnd|tr|đ|k|m)",
    re.IGNORECASE,
)
# Any Unicode letter (incl. accented VN). Residue after stripping the money
# skeleton: a pure price leaves none, "Gói 6 triệu" leaves "Gói".
_RESIDUE_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# A thousands-grouped number ("1.600.000", "129,000") inside a single cell marks a
# price NOTE ("Đơn giá: 1.600.000 đ"), not a section title — distinct from a bare
# incidental number like a year ("… cao cấp 2026", which IS a valid title). Shape.
_PRICE_NOTE_RE = re.compile(r"\d[.,]\d{3}")


def _nonempty(cells: list[str]) -> list[str]:
    return [c.strip() for c in cells if c and c.strip()]


def _is_pure_money(cell: str) -> bool:
    """True when *cell* is PURELY a money value (digits + separators + currency
    unit, NO descriptive word). Distinguishes a real PRICE ("899000", "1.499.000",
    "6 triệu", "1tr499", "1.5tr") from a NAME that merely contains a number ("Gói 6
    triệu") or a duration ("30 phút"). Shape-only, domain-neutral: strip the money
    skeleton (units + digits + separators); any remaining LETTER = a descriptive
    word, so the cell is a name, not a price."""
    c = cell.strip()
    if not c or parse_money_vn(c) is None:
        return False
    residue = _MONEY_UNIT_RE.sub(" ", c)
    residue = re.sub(r"[\d.,\s/]", "", residue)
    return not _RESIDUE_LETTER_RE.search(residue)


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
    if len(ne) < 2:  # noqa: PLR2004 — a header needs ≥2 column-label cells
        return False
    if any(_is_pure_money(c) for c in ne):
        return False
    # Most non-empty cells are short text labels (column names) — a "name | code |
    # price" header still qualifies even when one label carries a number ("date1").
    return sum(1 for c in ne if _is_label_like(c)) >= max(2, (len(ne) + 1) // 2)


def _is_header_continuation(top: list[str], bottom: list[str]) -> bool:
    """True when *bottom* is the SECOND row of a SPLIT header: it FILLS ≥1 of *top*'s
    EMPTY positions and does NOT overlap any of *top*'s filled cells.

    A spreadsheet whose column names span TWO stacked rows (row 1 = the first
    columns, row 2 = the later columns with empty leads) is a stacked 2-row header, e.g.
    ``[«», A, B, C, «», «», …]`` then ``[«», «», «», «», D, E, …]``.
    Merging the two BEFORE the table opens keeps the row-2 names
    real instead of collapsing to ``col_N``. Domain-neutral, shape-only: a real DATA
    row carries a value under a named column → it overlaps *top*'s filled cells →
    rejected, so this never fires on the happy single-row case."""
    m = min(len(top), len(bottom))
    fills = False
    for j in range(m):
        t, b = top[j].strip(), bottom[j].strip()
        if t and b:
            return False  # overlap → bottom is a data row, not a continuation
        if b and not t:
            fills = True
    return fills


def _merge_header_fill(top: list[str], bottom: list[str]) -> list[str]:
    """Fill *top*'s EMPTY positions from *bottom* (header-path gap-fill, SOTA
    Docling/TATR). Per column keep *top*'s label, else take *bottom*'s. Deterministic,
    no LLM, domain-neutral."""
    n = max(len(top), len(bottom))
    out: list[str] = []
    for j in range(n):
        t = top[j].strip() if j < len(top) else ""
        b = bottom[j].strip() if j < len(bottom) else ""
        out.append(t or b)
    return out


def _md_escape(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ").strip()


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    """L1 structure-recovery pre-pass — form-only, domain-neutral, no vocabulary.

    A raw spreadsheet is messy in shape-invariant ways that silently break the converter:
    - stray blank rows (a spacer after the header / between data rows) close the table
      → the following rows go headerless → ``col_N`` / product-code-as-name / row-loss;
    - a huge blank tail (Excel used-range) creates phantom boundaries;
    - merged-cell group labels leave continuation rows with an empty stub column.

    This pass fixes all three by SHAPE only: trim the blank head/tail (used-range),
    skip a blank run shorter than ``DEFAULT_TABLE_GAP_ROWS`` (a spacer) but keep ONE
    blank row for a longer run (a real table boundary the converter closes on), and
    forward-fill the LEADING contiguous sparse columns (rowspan / merged group label)
    — stopping at the first non-sparse leading column so a middle optional column is
    never over-filled.
    """
    def _blank(r: list[str]) -> bool:
        return not any((c or "").strip() for c in r)

    src = [list(r) for r in rows]
    while src and _blank(src[0]):
        src.pop(0)
    while src and _blank(src[-1]):
        src.pop()
    if not src:
        return []

    collapsed: list[list[str]] = []
    run = 0
    for r in src:
        if _blank(r):
            run += 1
            continue
        if run >= DEFAULT_TABLE_GAP_ROWS and collapsed:
            collapsed.append([])  # real gap → keep one blank = table boundary
        run = 0
        collapsed.append(list(r))

    # Forward-fill leading contiguous sparse columns (merged-cell / rowspan group
    # label). ``break`` at the first non-sparse leading column: a merged label is
    # the leftmost stub, so a fully-populated column (or a middle optional column)
    # is never over-filled.
    n_data = sum(1 for r in collapsed if r)
    width = max((len(r) for r in collapsed if r), default=0)
    for col in range(width):
        filled = sum(
            1 for r in collapsed if r and col < len(r) and (r[col] or "").strip()
        )
        if not 0 < filled < n_data:
            break
        last = ""
        for r in collapsed:
            if not r:  # boundary resets the fill
                last = ""
                continue
            # Only seed/fill from DATA rows (carry a money value). A header row —
            # incl. the empty-lead 2nd row of a stacked header — has no money, so it
            # is never filled; that keeps _is_header_continuation intact (else the
            # merged header collapses back to col_N).
            if not _has_money(r):
                continue
            if col < len(r) and (r[col] or "").strip():
                last = r[col]
            elif last:
                while len(r) <= col:
                    r.append("")
                r[col] = last
    return collapsed


def rows_to_structured_markdown(rows: list[list[str]]) -> str:  # noqa: PLR0915 — one linear state machine; splitting the row classifier hurts readability
    """Convert raw spreadsheet rows into section-bound structured markdown."""
    rows = _normalize_rows(rows)  # L1 structure-recovery: skip-blank/gap-K + forward-fill
    out: list[str] = []
    header: list[str] | None = None
    table_open = False

    def close_table() -> None:
        nonlocal table_open
        if table_open:
            out.append("")  # blank line after a table
            table_open = False

    def open_header(hdr_cells: list[str]) -> None:
        nonlocal header, table_open
        header = [(c or "").strip() or f"col{i + 1}" for i, c in enumerate(hdr_cells)]
        while header and not header[-1].strip():  # trim trailing empty header cols
            header.pop()
        cols = [_md_escape(h or f"col{i + 1}") for i, h in enumerate(header)]
        out.append("| " + " | ".join(cols) + " |")
        out.append("| " + " | ".join("---" for _ in cols) + " |")
        table_open = True

    norm = [[(c or "").strip() for c in raw] for raw in rows]
    n = len(norm)
    # Indices already absorbed as the SECOND row of a merged split-header — skipped
    # by the linear loop below so a continuation row is never re-emitted as DATA.
    consumed: set[int] = set()

    def _precedes_table(i: int) -> bool:
        """Lookahead: is the next non-empty row a header/data row? A LONG 1-cell
        line is a section title only when a table follows it (structure signal),
        not a hard word cap — a standalone long prose line stays a NOTE."""
        for j in range(i + 1, n):
            nj = _nonempty(norm[j])
            if not nj:
                return False
            return _looks_header(norm[j]) or _has_money(norm[j]) or len(nj) >= 2  # noqa: PLR2004 — a tabular row has ≥2 cells
        return False

    for i, cells in enumerate(norm):
        if i in consumed:
            continue
        ne = _nonempty(cells)

        # SEPARATOR — close any open table, table boundary.
        if not ne:
            close_table()
            header = None
            continue

        # SECTION_TITLE — one short non-money non-bullet cell. A LONG title (>8
        # words) is still a title when a table follows it (lookahead), so a real
        # multi-word section heading is no longer dropped (B-L1.2).
        if len(ne) == 1:
            only = ne[0]
            base = (
                len(only) <= _MAX_LABEL_CHARS * 2
                and not _is_pure_money(only)
                and not _PRICE_NOTE_RE.search(only)
                and only[:1] not in _BULLET_LEAD
                and not only.endswith((".", "…"))
            )
            short = len(only.split()) <= 8  # noqa: PLR2004 — short-title word cap (lookahead handles longer)
            if base and (short or _precedes_table(i)):
                close_table()
                header = None
                out.append(f"\n## {only}\n")
            else:
                # NOTE (prose / bullet / "Đơn giá: …") — keep as text.
                close_table()
                out.append(only)
            continue

        # SECTION-IN-HEADER — "<title> | <gap> | col | col": a section title sits in
        # col0 followed by an empty gap then ≥2 column labels (a colspan section row
        # above a header). Split into a section heading + a real header that SPANS
        # all columns (col0/gap become positional placeholders so DATA rows keep
        # their alignment). Shape-only — the gap right after col0 is the signal a
        # true header has not (B-L1.1).
        if (
            len(cells) >= 3  # noqa: PLR2004 — "title | gap | col" needs ≥3 cells
            and cells[0].strip()
            and not cells[1].strip()
            and not _is_pure_money(cells[0])
            and cells[0][:1] not in _BULLET_LEAD
            and _looks_header(cells[2:])
        ):
            close_table()
            header = None
            out.append(f"\n## {cells[0].strip()}\n")
            open_header(["", *cells[1:]])
            continue

        # HEADER — opens a new markdown table under the current section. Only when
        # NO table is currently open: once a table is open, a label-like all-text row
        # is a DATA row, not a new header (a SEPARATOR or SECTION_TITLE — both above —
        # already close the table at a real sub-table boundary). Without this guard an
        # all-text table whose data cells are short labels ("Miền Bắc | Hoạt động") had
        # every row re-promoted to its own one-row header, shredding row↔header binding.
        if _looks_header(cells) and not _has_money(cells) and not table_open:
            close_table()
            # MULTI-ROW HEADER MERGE — a label-only header with EMPTY cells whose
            # NEXT row is a label-only continuation filling those empties is a SPLIT
            # (2-row) header. Gap-fill the two into ONE header BEFORE open_header and
            # CONSUME the continuation, so the row-2 column names become real
            # labels instead of col_N (the col_N CRUX). A clean single-row header
            # has no empty cells → never merges (byte-identical).
            hdr_cells = cells
            if any(not c.strip() for c in cells):
                for nxt in range(i + 1, n):
                    if not _nonempty(norm[nxt]):
                        break  # blank row = table boundary, not a continuation
                    if (
                        _looks_header(norm[nxt])
                        and not _has_money(norm[nxt])
                        and _is_header_continuation(cells, norm[nxt])
                    ):
                        hdr_cells = _merge_header_fill(cells, norm[nxt])
                        consumed.add(nxt)
                    break
            open_header(hdr_cells)
            continue

        # DATA row.
        if table_open and header:
            vals = [(cells[k] if k < len(cells) else "") for k in range(len(header))]
            out.append("| " + " | ".join(_md_escape(v) for v in vals) + " |")
        else:
            # Data row with no open header → emit a bare pipe row so structure
            # (and the value↔position) is still preserved for the chunker.
            out.append("| " + " | ".join(_md_escape(c) for c in ne) + " |")
            table_open = False

    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _is_separator_row(s: str) -> bool:
    """A markdown table separator row: ``| --- | --- |`` (only dashes/colons)."""
    cells = [c.strip() for c in s.strip().strip("|").split("|")]
    return bool(cells) and all(bool(c) and set(c) <= {"-", ":"} for c in cells)


def split_markdown_to_row_chunks(markdown: str) -> list[str]:
    """Split section-bound structured markdown into ATOMIC per-row chunks.

    Each emitted chunk = the nearest ``## section`` heading + the table header
    + its separator + EXACTLY ONE data row, so every data row carries its own
    column labels and section context. The LLM therefore never sees two rows
    packed in one chunk → no cross-row value mis-binding (the bot reading the
    stock/price/date of a neighbouring row). A header with no data rows emits
    nothing (no orphan header-only chunk). Non-table prose lines become their
    own chunk under the current section. Domain-neutral — shape only, no
    vocabulary. Shared by every tabular parser (google_sheets, excel) so
    row-as-chunk is uniform across formats.
    """
    out: list[str] = []
    section = ""
    header = ""
    sep = ""
    for raw in markdown.splitlines():
        s = raw.strip()
        if not s:
            header = ""  # blank line closes the current table
            sep = ""
            continue
        if s.startswith("##"):
            section = s
            header = ""
            sep = ""
            continue
        if s.startswith("|"):
            if _is_separator_row(s):
                sep = raw
                continue
            if not header:
                header = raw  # first pipe row of a table = its header
                continue
            out.append("\n".join(p for p in (section, header, sep, raw) if p.strip()))
            continue
        out.append("\n".join(p for p in (section, s) if p.strip()))
    return out


__all__ = ["rows_to_structured_markdown", "split_markdown_to_row_chunks"]
