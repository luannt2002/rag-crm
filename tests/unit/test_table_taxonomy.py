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


def test_all_text_table_not_shredded_into_headers():
    """#1 (audit 2026-06-23) — an ALL-TEXT table (no money column) must stay ONE
    table: row 1 is the header, the rest are DATA rows. Before the fix each all-text
    data row was re-promoted to its own one-row header (the HEADER branch ran before
    the DATA branch with no 'table already open' guard), shredding row↔header binding
    on every all-text XLSX/CSV/Sheets ingest."""
    md = rows_to_structured_markdown([
        ["Khu vực", "Trạng thái"],
        ["Miền Bắc", "Hoạt động"],
        ["Miền Nam", "Tạm dừng"],
    ])
    # exactly ONE header-separator line — not one per data row
    assert sum(1 for ln in md.splitlines() if ln.strip().startswith("| ---")) == 1
    # the data values survive as table rows, not promoted to header labels
    assert "| Miền Bắc | Hoạt động |" in md
    assert "| Miền Nam | Tạm dừng |" in md


def test_all_text_table_with_textual_value_column_kept():
    """#1 — a money-shaped table whose value cell is TEXTUAL ('Liên hệ') must not
    re-open a header on the textual row; it stays a DATA row under the one header."""
    md = rows_to_structured_markdown([
        ["Dịch vụ", "Giá"],
        ["Item A", "Liên hệ"],
        ["Item B", "Miễn phí"],
    ])
    assert sum(1 for ln in md.splitlines() if ln.strip().startswith("| ---")) == 1
    assert "| Item A | Liên hệ |" in md
    assert "| Item B | Miễn phí |" in md


def test_multiword_total_row_not_an_entity():
    """#3 (audit 2026-06-23) — multi-word total labels ('Tổng tiền', 'Tạm tính',
    'Tổng giá', 'Grand total') must be rejected as aggregate rows, never surfaced as
    catalog entities (anti-HALLU conflate: a 'most expensive' query must not return the
    grand total). The prior exact-match set only had 'tong'/'tong cong'."""
    for total_label in ("Tổng tiền", "Tạm tính", "Tổng giá", "Grand total"):
        _, ents = _extract([
            ["Tên", "Giá"],
            ["Item A", "100000"],
            ["Item B", "200000"],
            [total_label, "300000"],
        ])
        names = {e.name for e in ents}
        assert total_label not in names, f"{total_label} leaked as a catalog entity"
        assert _priced(ents) == {"Item A": 100000, "Item B": 200000}


def test_total_lookalike_service_name_not_dropped():
    """#3 guard — a real service whose name merely STARTS with a total word
    ('Tổng hợp dịch vụ') must survive (exact-match rejection, NOT prefix-match —
    else we'd drop valid Coverage)."""
    _, ents = _extract([
        ["Tên", "Giá"],
        ["Tổng hợp dịch vụ chăm sóc", "500000"],
        ["Item B", "200000"],
    ])
    assert "Tổng hợp dịch vụ chăm sóc" in {e.name for e in ents}


def test_second_price_column_out_of_vocab_header_is_price_secondary():
    """#7 (audit) — a 2nd price column with an OUT-OF-VOCAB header must land in the
    NUMERIC price_secondary field, not a string attribute, so price-range / secondary
    SQL queries see it. A pure-money cell is a price regardless of header vocabulary."""
    _, ents = _extract([
        ["Tên", "Giá", "Phụ thu cuối tuần"],
        ["Item A", "100000", "150000"],
    ])
    a = next(e for e in ents if e.name == "Item A")
    assert a.price_primary == 100000
    assert a.price_secondary == 150000
