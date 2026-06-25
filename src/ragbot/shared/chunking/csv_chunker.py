"""CSV / price-table chunking (row-as-chunk with header context).

Extracted from the chunking god-file: detects CSV/table regions and emits one
chunk per row with the header prepended, plus a dual-index variant. Re-exported
by chunking/__init__ so existing imports stay valid."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from ragbot.shared.bootstrap_config import get_boot_config  # noqa: F401
from ragbot.shared.constants import *  # noqa: F401,F403
from ragbot.shared.chunking.vn_structural import *  # noqa: F401,F403
from ragbot.shared.chunking.analyze import *  # noqa: F401,F403
from ragbot.shared.chunking.blocks import *  # noqa: F401,F403

logger = structlog.get_logger(__name__)

def _chunk_table_csv(
    text: str,
    max_chunk_chars: int = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
) -> list[str]:
    """Row-as-chunk for CSV / column-aligned tabular data.

    Each data row becomes ONE chunk with the header row prepended so
    column names travel with the row. Oversized rows (> max_chunk_chars)
    are kept whole — splitting a row loses tuple semantics. Short rows
    are NOT bundled: bundling re-mixes adjacent tuples.

    NOTE (2026-06-17): a key:value rendering of rows was trialled here to lift
    NL retrieval on price tables; measured neutral-to-slightly-negative on the
    price-table load test (the failing questions are aggregation/numeric — "đắt nhất",
    "dưới 500k" — which dense embeddings can't solve via text reformatting), so
    it was reverted. The real fix is a per-table LLM description (RAG-Anything
    Technique 1, O(tables)) or query-time aggregation, not row text format.
    """
    if not text or not text.strip():
        return []
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return [text.strip()]
    header = lines[0]
    chunks: list[str] = []
    for row in lines[1:]:
        chunk_text = f"{header}\n{row}"
        if len(chunk_text) > max_chunk_chars:
            logger.warning(
                "table_csv_oversized_row_kept_whole",
                row_chars=len(row),
                max_chunk_chars=max_chunk_chars,
            )
        chunks.append(chunk_text)
    return chunks


def _detect_csv_regions(
    lines: list[str],
) -> tuple[list[str], int, int, list[str]]:
    """Identify the FIRST pre-table / table / post-table region.

    Single-region surface kept for back-compat with callers (legacy
    pre-Bug-#9 imports). The 260525 Bug #9 fix multi-table detector
    is :func:`_detect_csv_regions_all` which returns every region
    in document order; legacy single-region callers can keep using
    this helper and will simply see the first table.

    Returns ``(pre_lines, header_idx, last_data_idx, post_lines)``. When
    no run of ≥2 consecutive same-shape CSV lines is found, returns
    ``(lines, -1, -1, [])``.
    """
    regions = _detect_csv_regions_all(lines)
    if not regions:
        return (list(lines), -1, -1, [])
    first = regions[0]
    # Reconstruct legacy (pre, header_idx, last_data_idx, post)
    return (
        list(lines[: first.header_idx]),
        first.header_idx,
        first.last_data_idx,
        list(lines[first.last_data_idx + 1 :]),
    )


@dataclass(frozen=True, slots=True)
class _CsvRegion:
    """One detected CSV table region inside a multi-table document.

    Attributes:
        header_idx: ``lines`` index of the CSV header row (the first
            line of a run of ≥2 same-comma-count consecutive lines).
        last_data_idx: ``lines`` index of the last contiguous data row
            in this region (inclusive).
        pre: lines BETWEEN the previous region's ``last_data_idx + 1``
            and this region's ``header_idx`` (or [0..header_idx] for
            the first region). Captures intro paragraphs / boundary
            heading text for the synthetic header chunk.
        post: lines AFTER ``last_data_idx`` up to (but not including)
            the next region's ``header_idx``, OR to the end of doc
            for the last region. Captures trailing notes for the
            synthetic footer chunk.
    """

    header_idx: int
    last_data_idx: int
    pre: list[str]
    post: list[str]


def _is_csv_shape_line(line: str) -> bool:
    """A line qualifies as CSV-shape when it has ≥DEFAULT_CSV_MIN_COMMAS
    commas AND ≥``DEFAULT_TABLE_CSV_MIN_NON_EMPTY_CELLS`` non-empty cells
    when split by commas.

    260525 Bug #9-followup — prose lines that happen to end with stray
    trailing commas share the comma count of real CSV rows but carry
    only ONE non-empty cell. Counting commas alone made the detector
    classify such prose as table headers, corrupting region
    boundaries.
    """
    if line.count(",") < DEFAULT_CSV_MIN_COMMAS:
        return False
    non_empty_cells = sum(1 for cell in line.split(",") if cell.strip())
    return non_empty_cells >= DEFAULT_TABLE_CSV_MIN_NON_EMPTY_CELLS


def _detect_csv_regions_all(lines: list[str]) -> list[_CsvRegion]:
    """Identify ALL CSV table regions in document order.

    Bug #9 fix (260525): multi-table documents (e.g. a single Sheets
    export containing both "Giá lẻ" + "Combo" tables) were silently
    truncated by the legacy detector which kept only the LONGEST run
    of consecutive same-comma-count lines. Every shorter table — and
    any data it carries — fell into the discarded ``post`` region.

    Bug #9-followup (260525): prose bullet lines with stray trailing
    commas (",,,") were also treated as CSV-shape because the original
    test counted commas only. The tightened ``_is_csv_shape_line``
    helper now requires ≥``DEFAULT_TABLE_CSV_MIN_NON_EMPTY_CELLS``
    non-empty cells too, rejecting prose like "- <bullet text>,,,"
    that no longer pollutes region boundaries.

    New behaviour: scan lines once, emit one ``_CsvRegion`` per run of
    ≥2 consecutive CSV-shape lines sharing the same comma count.
    Boundary text between two regions is split — assigned to the
    trailing ``post`` of region N AND the leading ``pre`` of region
    N+1 — so neither synthetic chunk loses the heading that introduces
    / closes a table.

    Empty input → empty list (caller treats as no-table).
    """
    if not lines:
        return []

    regions: list[_CsvRegion] = []
    n = len(lines)
    i = 0
    while i < n:
        if not _is_csv_shape_line(lines[i]):
            i += 1
            continue
        c = lines[i].count(",")
        j = i
        while (
            j + 1 < n
            and lines[j + 1].count(",") == c
            and _is_csv_shape_line(lines[j + 1])
        ):
            j += 1
        run_len = j - i + 1
        if run_len >= 2:
            regions.append(
                _CsvRegion(
                    header_idx=i,
                    last_data_idx=j,
                    pre=[],   # filled in pass 2
                    post=[],  # filled in pass 2
                ),
            )
        i = j + 1

    if not regions:
        return []

    # Pass 2 — assign boundary text to pre/post of adjacent regions.
    finalized: list[_CsvRegion] = []
    for idx, r in enumerate(regions):
        if idx == 0:
            pre_lines = list(lines[: r.header_idx])
        else:
            prev_end = regions[idx - 1].last_data_idx + 1
            pre_lines = list(lines[prev_end : r.header_idx])
        if idx == len(regions) - 1:
            post_lines = list(lines[r.last_data_idx + 1 :])
        else:
            # Boundary lines go to BOTH this region's post AND the next
            # region's pre — same lines duplicated. They are typically
            # the "BẢNG GIÁ ..." heading for the next table, which is
            # contextually relevant for both the closing footer of
            # region N (so retrieval surfaces the boundary marker) and
            # the opening header of region N+1.
            next_start = regions[idx + 1].header_idx
            post_lines = list(lines[r.last_data_idx + 1 : next_start])
        finalized.append(
            _CsvRegion(
                header_idx=r.header_idx,
                last_data_idx=r.last_data_idx,
                pre=pre_lines,
                post=post_lines,
            ),
        )
    return finalized


def _doc_table_header(lines: list[str]) -> str:
    """The document's column-header row to prepend to every CSV row chunk.

    ``_detect_csv_regions_all`` groups lines by identical comma-count, so a
    region's ``header_idx`` line is the FIRST line of a same-shape run — NOT
    inherently a header. When the real column header has a different comma count
    (e.g. a Sheets export whose data rows carry extra image-URL columns the
    header lacks), each region's first line is a DATA row; prepending it to
    every row duplicated one data row across 100+ chunks (2026-06-13
    xe-warehouse bug: 116 chunks all prefixed with the same ``2-R15 175/65``
    row → embedding collapse).

    Fix: use ONE document-level header — the FIRST CSV-shape line in the
    document (the column names at the top) — for every row chunk, consistently
    across regions. The header repeating across chunks is intended (column
    names travel with each row, ~30 chars); duplicating a 200-char data row is
    the bug this removes. Falls back to ``lines[0]`` when no CSV-shape line
    exists.
    """
    for ln in lines:
        if _is_csv_shape_line(ln):
            return ln
    return lines[0]


def _is_empty_csv_row(line: str) -> bool:
    """A CSV row carries NO data when stripped of separators/whitespace it is
    empty (``",,,,"``, ``", , ,"``). Such rows (common in spreadsheet exports
    with trailing blank rows) produce a zero-signal embedding and waste tokens
    — drop them at chunk time. A row with even one non-empty cell is kept.
    """
    return not re.sub(r"[,\s]", "", line)


def _chunk_table_csv_with_context(
    text: str,
    max_chunk_chars: int = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
    *,
    header_footer_enabled: bool = False,
    header_chunk_sample_rows: int = DEFAULT_TABLE_CSV_HEADER_CHUNK_SAMPLE_ROWS,
    footer_chunk_sample_rows: int = DEFAULT_TABLE_CSV_FOOTER_CHUNK_SAMPLE_ROWS,
    pre_min_chars: int = DEFAULT_TABLE_CSV_PRE_MIN_CHARS,
    post_min_chars: int = DEFAULT_TABLE_CSV_POST_MIN_CHARS,
) -> list[str]:
    """Row-as-chunk + optional header/footer synthetic chunks.

    Default behaviour (``header_footer_enabled=False``) is byte-identical
    to :func:`_chunk_table_csv` — every data row becomes one chunk with
    the header row prepended.

    When ``header_footer_enabled=True`` AND the input has a non-trivial
    pre-table region (≥1 line with length > ``pre_min_chars``), an extra
    header chunk is emitted:
        pre_text + csv_header + first ``header_chunk_sample_rows`` data rows
    Same shape applies to ``post`` region → footer chunk.

    The synthetic chunks let retrieval surface the document's topic
    overview ("what is this table about") and any trailing notes
    (promotions, warranty, source attribution) that pure row-as-chunk
    discards.

    Synthetic chunks oversized beyond ``max_chunk_chars`` are dropped
    rather than emitted half-formed — the row chunks still cover the
    full data set.
    """
    if not text or not text.strip():
        return []
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return [text.strip()]

    if not header_footer_enabled:
        # Flag-off path — delegate to the pure row-as-chunk implementation
        # so output stays byte-identical to the pre-flag default.
        return _chunk_table_csv(text, max_chunk_chars=max_chunk_chars)

    # 260525 Bug #9 fix — detect EVERY CSV region (multi-table support).
    regions = _detect_csv_regions_all(lines)

    # No real table detected — degrade to a single chunk so callers don't
    # silently lose the content of a misclassified document.
    if not regions:
        return ["\n".join(lines)]

    chunks: list[str] = []
    doc_header = _doc_table_header(lines)
    for region in regions:
        region_lines = lines[region.header_idx : region.last_data_idx + 1]
        # Treat region's first line as DATA (header is the doc-level column row);
        # drop the doc header if it falls inside the region + drop no-data rows.
        data_rows = [
            r for r in region_lines
            if r != doc_header and not _is_empty_csv_row(r)
        ]
        if not data_rows:
            continue  # empty table region → emit nothing
        header = doc_header

        pre_non_trivial = any(len(ln) > pre_min_chars for ln in region.pre)
        if pre_non_trivial:
            sample = data_rows[:header_chunk_sample_rows]
            header_chunk = "\n".join([*region.pre, header, *sample])
            if len(header_chunk) <= max_chunk_chars:
                chunks.append(header_chunk)
            else:
                logger.warning(
                    "table_csv_header_chunk_oversized_dropped",
                    pre_chars=sum(len(ln) for ln in region.pre),
                    sample_rows=len(sample),
                    max_chunk_chars=max_chunk_chars,
                )

        # Row chunks — same shape as :func:`_chunk_table_csv` (header + 1 row).
        for row in data_rows:
            chunk_text = f"{header}\n{row}"
            if len(chunk_text) > max_chunk_chars:
                logger.warning(
                    "table_csv_oversized_row_kept_whole",
                    row_chars=len(row),
                    max_chunk_chars=max_chunk_chars,
                )
            chunks.append(chunk_text)

        post_non_trivial = any(len(ln) > post_min_chars for ln in region.post)
        if post_non_trivial:
            sample = data_rows[-footer_chunk_sample_rows:] if data_rows else []
            footer_chunk = "\n".join([header, *sample, *region.post])
            if len(footer_chunk) <= max_chunk_chars:
                chunks.append(footer_chunk)
            else:
                logger.warning(
                    "table_csv_footer_chunk_oversized_dropped",
                    post_chars=sum(len(ln) for ln in region.post),
                    sample_rows=len(sample),
                    max_chunk_chars=max_chunk_chars,
                )

    return chunks


def _chunk_table_dual_index(
    text: str,
    max_chunk_chars: int = DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
    *,
    group_max_chars: int = DEFAULT_TABLE_DUAL_GROUP_MAX_CHARS,
) -> list[str]:
    """Dual-index a CSV/column table: whole-table group chunk(s) + row chunks.

    Per detected CSV region this emits, in order:
      1. **Group chunk(s)** — header + as many consecutive data rows as fit
         under ``group_max_chars``. A table that fits becomes ONE group chunk
         (the whole table), so aggregation / "list-all" queries retrieve every
         row at once instead of missing rows after the top-k / rerank cap. A
         lone trailing row is folded into the previous group so coverage is by
         multi-row blocks, not a stray single.
      2. **Row chunks** — header + one data row each (identical to
         :func:`_chunk_table_csv`) so precise single-row lookup still works.

    Non-table input degrades to the pure row-as-chunk path (no spurious
    group chunk).
    """
    if not text or not text.strip():
        return []
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return [text.strip()]

    regions = _detect_csv_regions_all(lines)
    if not regions:
        return _chunk_table_csv(text, max_chunk_chars=max_chunk_chars)

    chunks: list[str] = []
    doc_header = _doc_table_header(lines)
    for region in regions:
        region_lines = lines[region.header_idx : region.last_data_idx + 1]
        data_rows = [
            r for r in region_lines
            if r != doc_header and not _is_empty_csv_row(r)
        ]
        if not data_rows:
            continue  # empty table region → emit nothing
        header = doc_header

        # 1. Pack rows into header-prefixed group chunks under the cap.
        groups: list[list[str]] = []
        cur: list[str] = []
        cur_len = len(header)
        for row in data_rows:
            add = len(row) + 1  # newline
            if cur and cur_len + add > group_max_chars:
                groups.append(cur)
                cur = []
                cur_len = len(header)
            cur.append(row)
            cur_len += add
        if cur:
            # Avoid a degenerate lone trailing single-row group — merge it back
            # so every group chunk is a meaningful multi-row block (when the
            # table has >1 row).
            if len(cur) == 1 and groups:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        for g in groups:
            chunks.append("\n".join([header, *g]))

        # 2. Row chunks (precise lookup) — same shape as _chunk_table_csv.
        for row in data_rows:
            chunk_text = f"{header}\n{row}"
            if len(chunk_text) > max_chunk_chars:
                logger.warning(
                    "table_dual_index_oversized_row_kept_whole",
                    row_chars=len(row),
                    max_chunk_chars=max_chunk_chars,
                )
            chunks.append(chunk_text)

    return chunks


# ---------------------------------------------------------------------------
# Strategy: Recursive with table protection (default)
# ---------------------------------------------------------------------------


__all__ = [
    "_chunk_table_csv",
    "_detect_csv_regions",
    "_CsvRegion",
    "_is_csv_shape_line",
    "_detect_csv_regions_all",
    "_doc_table_header",
    "_is_empty_csv_row",
    "_chunk_table_csv_with_context",
    "_chunk_table_dual_index",
]
