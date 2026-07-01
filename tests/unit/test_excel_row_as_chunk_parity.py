"""[Phase 1] XLSX row-as-chunk parity with Google Sheets.

ExcelOpenpyxlParser emitted the WHOLE workbook as ONE markdown blob, so a large
sheet was embedded as a single Lost-in-the-Middle chunk and the size-chunker
could pack two rows together (cross-row value mis-bind). GoogleSheetsParser
already splits into one atomic chunk per data row via
``split_markdown_to_row_chunks``. This pins the parity: excel now emits one
row-chunk per data row, each carrying its own header — while the deterministic
stats extraction stays intact (col_N-free, forward-filled).
"""
from __future__ import annotations

import asyncio
import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from ragbot.infrastructure.parser.excel_openpyxl_parser import ExcelOpenpyxlParser
from ragbot.shared.document_stats import parse_table_chunks
from ragbot.shared.tabular_markdown import split_markdown_to_row_chunks


def _xlsx_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_emits_one_chunk_per_data_row() -> None:
    """3 data rows → 3 atomic chunks (was 1 blob), each with the header."""
    data = _xlsx_bytes([
        ["Nhóm", "Tên", "Giá"],
        ["", "", ""],                 # blank row after header (dropped)
        ["Kho A", "Sản phẩm X", 100000],
        ["", "Sản phẩm Y", 200000],   # empty group cell → forward-fill
        ["Kho B", "Sản phẩm Z", 300000],
    ])
    docs = asyncio.run(ExcelOpenpyxlParser().parse(data, file_name="t.xlsx"))
    assert len(docs) == 3, f"expected 3 atomic row-chunks, got {len(docs)}"
    for d in docs:
        assert "| Nhóm | Tên | Giá |" in d["content"], "each row-chunk keeps its header"
        assert d["metadata"]["parser"] == "excel_openpyxl"


def test_excel_stats_extraction_still_clean() -> None:
    """Row-chunk split must not regress the deterministic entity extraction."""
    data = _xlsx_bytes([
        ["Nhóm", "Tên", "Giá"],
        ["Kho A", "Sản phẩm X", 100000],
        ["", "Sản phẩm Y", 200000],
    ])
    docs = asyncio.run(ExcelOpenpyxlParser().parse(data, file_name="t.xlsx"))
    ents = parse_table_chunks(docs)
    assert len(ents) == 2
    for e in ents:
        assert not any(str(k).startswith("col_") for k in (e.attributes or {})), (
            "no col_N placeholder"
        )
    # forward-fill: the empty group cell inherits the row above
    assert {e.category for e in ents} == {"Kho A"}


def test_split_helper_header_only_emits_nothing() -> None:
    """A header with no data rows must not emit an orphan header-only chunk."""
    md = "## S\n| A | B |\n| --- | --- |\n"
    assert split_markdown_to_row_chunks(md) == []
