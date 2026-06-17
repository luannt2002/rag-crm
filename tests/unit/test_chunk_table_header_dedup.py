"""Table-CSV duplicate-prefix fix (2026-06-13).

Bug: ``_detect_csv_regions_all`` groups lines by comma-count, so a region's
first line is the first of a same-shape RUN — not inherently a header. On a
multi-section sheet whose column header has a different comma count than data
rows (e.g. data rows carry extra image-URL columns), each region's first DATA
row became the prepended "header", duplicating one row across 100+ chunks
(xe-warehouse sheet → 116 chunks all prefixed with the same ``2-R15 175/65``
row, collapsing the embeddings).

Fix: prepend ONE document-level column header (``_doc_table_header``) to every
row chunk; drop no-data rows (``_is_empty_csv_row``).
"""
from __future__ import annotations

from collections import Counter

from ragbot.shared.chunking import (
    _chunk_table_csv_with_context,
    _chunk_table_dual_index,
    _doc_table_header,
    _is_empty_csv_row,
)

# Multi-section sheet: real column header (row 0) has FEWER columns than the
# data rows (which carry extra URL columns) → region detector excludes the
# header from the data run, reproducing the bug condition.
_SHEET = (
    "Kho,Ma,Ten\n"
    ",LANDSPIDER,2-R13 155,Lop 155R13,26,,http://img/a,http://img/b\n"
    ",LANDSPIDER,2-R13 165,Lop 165R13,26,,http://img/c,http://img/d\n"
    ",LANDSPIDER,2-R13 175,Lop 175R13,26,,http://img/e,http://img/f\n"
    ",LANDSPIDER,2-R14 165,Lop 165R14,26,,http://img/g,http://img/h\n"
    ",,,,,,,\n"  # empty trailing row → must be dropped
)


def test_doc_header_is_column_names_not_data_row() -> None:
    lines = [ln for ln in _SHEET.split("\n") if ln.strip()]
    assert _doc_table_header(lines) == "Kho,Ma,Ten"


def test_empty_csv_row_detection() -> None:
    assert _is_empty_csv_row(",,,,")
    assert _is_empty_csv_row(", , , ")
    assert _is_empty_csv_row("")
    assert not _is_empty_csv_row(",LANDSPIDER,2-R13,")


def _max_first_line_dup(chunks: list[str]) -> int:
    return Counter(c.split("\n")[0] for c in chunks).most_common(1)[0][1]


def test_dual_index_no_data_row_duplicated_as_header() -> None:
    chunks = _chunk_table_dual_index(_SHEET)
    # The header (column names) repeats — that is correct. But NO chunk's first
    # line may be a DATA row, and the data rows themselves must be distinct.
    for c in chunks:
        assert c.split("\n")[0] == "Kho,Ma,Ten", f"non-header prefix: {c[:50]!r}"
    # Row chunks: second line is the data row — each distinct (no 100x dup).
    row_lines = [c.split("\n")[1] for c in chunks if c.count("\n") == 1]
    assert len(set(row_lines)) == len(row_lines), "data rows duplicated across chunks"


def test_csv_with_context_drops_empty_rows() -> None:
    chunks = _chunk_table_csv_with_context(_SHEET, header_footer_enabled=True)
    # The all-empty trailing row (",,,,,,,") must not appear as a data chunk.
    for c in chunks:
        data = c.split("\n")[-1]
        assert not _is_empty_csv_row(data), f"empty row leaked into chunk: {c!r}"
