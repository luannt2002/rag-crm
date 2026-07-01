"""[Phase 1 — L1 brittleness] Golden acceptance oracle for messy real-world
spreadsheets. Feeds each variation through the REAL pipeline
(rows_to_structured_markdown → split_markdown_to_row_chunks → parse_table_chunks) and
asserts structure is recovered (real name, header-labeled attrs, no dropped rows,
forward-filled category) instead of silently degrading to col_N / code-as-name /
row-loss. Form-only / domain-neutral — locks "sửa format là lỗi" so it never
recurs silently.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks
from ragbot.shared.tabular_markdown import (
    rows_to_structured_markdown,
    split_markdown_to_row_chunks,
)


def _entities(rows: list[list[str]]):
    md = rows_to_structured_markdown(rows)
    chunks = [{"content": c, "raw_chunk": c} for c in split_markdown_to_row_chunks(md)]
    return parse_table_chunks(chunks)


_HDR = ["Mã", "Tên sản phẩm", "Số lượng", "Giá"]


def test_baseline_clean_still_works() -> None:
    ents = _entities([_HDR, ["A01", "Áo thun cotton", "50", "100000"]])
    assert len(ents) == 1
    assert ents[0].name == "Áo thun cotton"
    assert "Số lượng" in ents[0].attributes


def test_case02_blank_row_after_header_keeps_header() -> None:
    # A stray blank row right after the header must NOT close the table.
    ents = _entities([_HDR, ["", "", "", ""], ["A01", "Áo thun cotton", "50", "100000"]])
    assert len(ents) == 1
    assert ents[0].name == "Áo thun cotton", "name must be the product, not the code (header lost)"
    assert "col_" not in " ".join(ents[0].attributes.keys()), "columns must keep header labels, not col_N"


def test_case03_blank_row_mid_data_drops_no_row() -> None:
    ents = _entities([
        _HDR,
        ["A01", "Áo thun cotton", "50", "100000"],
        ["", "", "", ""],
        ["A02", "Quần jean nam", "30", "200000"],
    ])
    names = {e.name for e in ents}
    assert names == {"Áo thun cotton", "Quần jean nam"}, "no data row may be silently dropped"


def test_case04_merged_group_label_forward_fills_category() -> None:
    grouped = [
        ["Nhóm", "Mã", "Tên sản phẩm", "Giá"],
        ["Áo", "A01", "Áo thun", "100000"],
        ["", "A02", "Áo khoác", "200000"],   # merged-cell continuation (col0 empty)
        ["Quần", "Q01", "Quần jean", "300000"],
        ["", "Q02", "Quần kaki", "400000"],
    ]
    cats = [e.category for e in _entities(grouped)]
    assert cats == ["Áo", "Áo", "Quần", "Quần"], "merged-cell group label must forward-fill down"


def test_trailing_blank_rows_trimmed() -> None:
    # Excel export empty tail must not create phantom entities or break parsing.
    ents = _entities([_HDR, ["A01", "Áo thun cotton", "50", "100000"]] + [["", "", "", ""]] * 12)
    assert len(ents) == 1 and ents[0].name == "Áo thun cotton"


def test_optional_empty_cell_not_over_filled() -> None:
    # A genuinely-populated column with a single missing value must NOT be
    # forward-filled from a neighbour (over-propagation guard).
    rows = [
        ["Mã", "Tên", "Ghi chú", "Giá"],
        ["A01", "Áo", "hàng mới", "100000"],
        ["A02", "Quần", "", "200000"],   # 'Ghi chú' empty here — must stay empty, not "hàng mới"
    ]
    ents = _entities(rows)
    a02 = next(e for e in ents if e.name == "Quần")
    assert a02.attributes.get("Ghi chú", "") != "hàng mới", "optional column must not over-fill"
