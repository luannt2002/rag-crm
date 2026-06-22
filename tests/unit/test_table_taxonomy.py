"""Regression gate for table-structure robustness (L1 converter + L3 stats).

Locks the fixes from plans/20260622-table-taxonomy-robustness: a tenant's
spreadsheet in ANY of these shapes must extract correctly (relational) or at least
not pollute the stats index with garbage (non-relational). Domain-neutral synthetic
fixtures — generic "Item A" / "Region" / years, NO tenant vocabulary — so the test
proves SHAPE-based handling, not memorised data.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks
from ragbot.shared.tabular_markdown import rows_to_structured_markdown


def _extract(rows: list[list[str]]):
    md = rows_to_structured_markdown(rows)
    return md, parse_table_chunks([{"content": md}])


def _priced(ents) -> dict[str, int]:
    return {e.name: e.price_primary for e in ents if e.price_primary}


def test_simple_top_header_relational():
    _, ents = _extract([["STT", "Tên", "Giá"], ["1", "Item A", "100000"], ["2", "Item B", "200000"]])
    assert _priced(ents) == {"Item A": 100000, "Item B": 200000}


def test_money_as_name_not_dropped():
    """P1.1 — a NAME containing a money phrase ("Gói 6 triệu") keeps its real price."""
    _, ents = _extract([["Tên gói", "Giá"], ["Gói 6 triệu", "6000000"], ["Gói cơ bản", "2000000"]])
    assert _priced(ents) == {"Gói 6 triệu": 6000000, "Gói cơ bản": 2000000}


def test_stub_category_column_binds_not_becomes_name():
    """P1.2 — a category/stub column is the CATEGORY, the next column is the NAME."""
    _, ents = _extract([
        ["Nhóm", "Tên", "Giá"],
        ["Cao cấp", "Item A", "100000"],
        ["Cao cấp", "Item B", "200000"],
        ["Phổ thông", "Item C", "50000"],
    ])
    by_name = {e.name: (e.price_primary, e.category) for e in ents}
    assert by_name == {
        "Item A": (100000, "Cao cấp"),
        "Item B": (200000, "Cao cấp"),
        "Item C": (50000, "Phổ thông"),
    }


def test_rowspan_blank_group_forward_fills_category():
    """P1.2 — a blank stub cell (rowspan continuation) inherits the prior category."""
    _, ents = _extract([
        ["Nhóm", "Tên", "Giá"],
        ["Cao cấp", "Item A", "100000"],
        ["", "Item B", "200000"],
        ["Phổ thông", "Item C", "50000"],
    ])
    assert {e.name: e.category for e in ents} == {
        "Item A": "Cao cấp", "Item B": "Cao cấp", "Item C": "Phổ thông",
    }


def test_total_row_not_an_entity():
    """P2.1 — a 'Tổng cộng' aggregate row is not promoted to a catalog entity."""
    _, ents = _extract([["Tên", "Giá"], ["Item A", "100000"], ["Item B", "200000"], ["Tổng cộng", "300000"]])
    names = {e.name for e in ents}
    assert "Tổng cộng" not in names
    assert _priced(ents) == {"Item A": 100000, "Item B": 200000}


def test_transposed_table_no_garbage_entity():
    """P2.1 — a transposed sheet must not promote the row-label "Giá" to an entity."""
    _, ents = _extract([
        ["Thuộc tính", "Item A", "Item B"],
        ["Giá", "100000", "200000"],
        ["Bảo hành", "12", "24"],
    ])
    assert "Giá" not in {e.name for e in ents}


def test_key_value_vertical_no_garbage_entity():
    """P2.1 — a vertical key-value form must not surface "Giá" as a priced entity."""
    _, ents = _extract([["Tên", "Item A"], ["Giá", "100000"], ["Mô tả", "chất lượng cao"]])
    assert "Giá" not in _priced(ents)


def test_section_in_header_split():
    """P1.3 — '<title>,,col,col' splits into a section heading + a real header."""
    md, ents = _extract([["Gói dịch vụ A", "", "Thời gian", "Giá"], ["1", "Item A", "30 phút", "100000"]])
    assert "## Gói dịch vụ A" in md
    assert _priced(ents) == {"Item A": 100000}
    assert next(e for e in ents if e.name == "Item A").category == "Gói dịch vụ A"


def test_long_section_title_with_year_kept():
    """P1.4 — a long title containing an incidental year ('… 2026') stays a section."""
    md, ents = _extract([
        ["Bảng giá dịch vụ chăm sóc chuyên sâu cao cấp 2026"],
        ["Tên", "Giá"],
        ["Item A", "100000"],
    ])
    assert "## Bảng giá dịch vụ chăm sóc chuyên sâu cao cấp 2026" in md
    assert _priced(ents) == {"Item A": 100000}


def test_two_col_category_token_is_the_name_not_a_stub():
    """Regression — a 2-col 'Vùng | Giá' table: the category-token column IS the
    entity name (no separate name column to fall back to), must NOT be dropped."""
    _, ents = _extract([["Vùng", "Giá"], ["Mép", "129000"], ["Nách", "199000"]])
    assert _priced(ents) == {"Mép": 129000, "Nách": 199000}


def test_multi_section_mixed_2col_and_3col_all_extracted():
    """Regression — a sheet stacking a 3-col table and a 2-col category-token table
    extracts ALL rows with the right section category."""
    _, ents = _extract([
        ["Dịch vụ chăm sóc da"], ["STT", "Tên", "Giá"], ["1", "Item A", "100000"],
        [""],
        ["Dịch vụ triệt lông"], ["Vùng", "Giá"], ["Mép", "129000"], ["Nách", "199000"],
    ])
    by_name = {e.name: e.category for e in ents}
    assert by_name == {
        "Item A": "Dịch vụ chăm sóc da",
        "Mép": "Dịch vụ triệt lông",
        "Nách": "Dịch vụ triệt lông",
    }


def test_price_note_single_cell_not_a_section_title():
    """Guard — a one-cell price NOTE ('Giá 1 buổi: 1.600.000 đ') is not a heading."""
    md, _ = _extract([["Tên", "Giá"], ["Item A", "100000"], ["Giá 1 buổi: 1.600.000 đ"]])
    assert "## Giá 1 buổi" not in md
