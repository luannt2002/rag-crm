"""[T1-Smartness] Multi-row / split header merge (header-path concatenation).

SOTA table preprocessing (Docling / TATR / MixRAG H-RCL): a spreadsheet whose header
is split across TWO rows — row 1 holds the first columns, row 2 holds later columns
(with empty leading cells) — must be merged BEFORE role detection, else the row-2
column NAMES (date1 / hình ảnh / Tồn) are lost and those columns become unlabeled
``col_N``. The value is then captured but the LLM can't map "date" → "col_4" → refuses
despite having the data (the xe N5 0/5 bug).

Merge is gap-fill + hierarchical concat, deterministic, NO LLM, domain-neutral.
A normal single-row header (no empty cells) is NEVER merged → happy-case unchanged.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks


def _attrs(entities: list) -> dict:
    merged: dict = {}
    for e in entities:
        merged.update(e.attributes)
    return merged


def test_two_row_split_header_merges_and_labels_columns() -> None:
    # Row 1 = first cols; row 2 = later cols (date/image) with empty leads. The xe shape.
    content = (
        ",Tên kho,Mã hàng,Tên hàng,,,\n"
        ",,,,date1,date2,hình ảnh\n"
        "Kho A,Kho lốp,2-R16 195/65 NEO,Lốp NEOTERRA 195/65R16,26,27,http://img/x\n"
    )
    ents = parse_table_chunks([{"content": content}])
    assert ents, "no entity extracted"
    names = [e.name for e in ents]
    assert "Lốp NEOTERRA 195/65R16" in names, f"name col mis-bound: {names}"
    attrs = _attrs(ents)
    # The date/image columns must be labelled by their REAL row-2 names, not col_N.
    assert any("date1" in k.lower() for k in attrs), f"date1 lost to col_N: {list(attrs)}"
    assert any("hình ảnh" in k.lower() or "hinh anh" in k.lower() for k in attrs), (
        f"image column lost to col_N: {list(attrs)}"
    )
    # And the value rides under the real label.
    assert "26" in {str(v) for v in attrs.values()}, f"date value dropped: {attrs}"


def test_single_row_header_not_merged_happy_case() -> None:
    # No empty cells in the header → never merged → byte-identical to before.
    content = "Tên,Nhóm,Giá\nGói A,Cao cấp,500000\n"
    ents = parse_table_chunks([{"content": content}])
    assert ents and ents[0].name == "Gói A"
    assert ents[0].price_primary == 500000


def test_text_data_row_not_falsely_merged_into_header() -> None:
    # Header has NO gaps; the following text-only data row (no money) must NOT be
    # swallowed as a second header row.
    content = "Tên,Mô tả\nÁo thun nam,Mô tả sản phẩm khá dài nhiều chữ\n"
    ents = parse_table_chunks([{"content": content}])
    assert ents and ents[0].name == "Áo thun nam"
    attrs = _attrs(ents)
    assert any("dài nhiều chữ" in str(v) for v in attrs.values()), (
        f"description row wrongly merged as header: {attrs}"
    )
