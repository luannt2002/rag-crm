"""Tabular → structured markdown + B3 section→category binding (AdapChunk L1+B3).

The systemic "trắng trơn" bug: a multi-table sheet lost each sub-table's section
title, so a triệt-lông row ("Mép | 129000") had no link to "triệt lông" and a
"triệt lông" query returned 0. Fix: convert to structured markdown (`## section`
+ `| table |`) and bind the section heading → entity_category at extraction.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks
from ragbot.shared.tabular_markdown import rows_to_structured_markdown

# A multi-table sheet: 2 sub-tables, each with its own SECTION TITLE + header.
_ROWS = [
    ["Dịch vụ chăm sóc da", "", ""],
    ["STT", "Tên dịch vụ", "Giá 1 buổi"],
    ["1", "Chăm sóc da chuyên sâu", "700000"],
    ["", "", ""],
    ["Dịch vụ triệt lông", "", ""],
    ["- Công nghệ Diode Laser an toàn", "", ""],  # description bullet (NOT a title)
    ["STT", "Vùng triệt", "Giá buổi lẻ"],
    ["1", "Mép", "129000"],
    ["2", "Mặt", "249000"],
]


def test_converter_binds_section_title_above_each_table() -> None:
    md = rows_to_structured_markdown(_ROWS)
    assert "## Dịch vụ chăm sóc da" in md
    assert "## Dịch vụ triệt lông" in md
    # The zone rows land UNDER the triệt-lông heading as a markdown table.
    i_heading = md.find("## Dịch vụ triệt lông")
    i_mep = md.find("| Mép |")
    assert 0 <= i_heading < i_mep, "Mép must appear AFTER its section heading"
    assert "| Mép | 129000 |" in md


def test_extraction_binds_section_heading_to_category() -> None:
    md = rows_to_structured_markdown(_ROWS)
    ents = {e.name: e for e in parse_table_chunks([{"content": md}])}
    assert "Mép" in ents and "Mặt" in ents
    # B3: the zone inherits its service section as category → findable by "triệt lông".
    assert ents["Mép"].category == "Dịch vụ triệt lông"
    assert ents["Mặt"].category == "Dịch vụ triệt lông"
    assert ents["Mép"].price_primary == 129000  # noqa: PLR2004 — literal corpus value
    # A description bullet must NOT have leaked in as the category.
    assert "Diode Laser" not in (ents["Mép"].category or "")


def test_chamsoc_zone_not_cross_contaminated() -> None:
    ents = {e.name: e for e in parse_table_chunks(
        [{"content": rows_to_structured_markdown(_ROWS)}]
    )}
    # The chăm-sóc-da row keeps its OWN section, not the triệt-lông one.
    assert ents["Chăm sóc da chuyên sâu"].category == "Dịch vụ chăm sóc da"


def test_chunker_reattaches_section_heading_then_extraction_binds() -> None:
    """Full chain: structured markdown → smart_chunk → parse_table_chunks.

    The size-based splitter can sever a '## section' from its table; the
    contextual-chunking re-attach (AdapChunk B3) keeps every chunk self-describing
    so the category survives end-to-end even when heading + table land apart.
    """
    from ragbot.shared.chunking import smart_chunk

    # Pad the chăm-sóc section so the splitter is likely to cut before the
    # triệt-lông table — exercising the re-attach path.
    rows = [["Dịch vụ chăm sóc da", "", ""], ["STT", "Tên", "Giá"]]
    rows += [[str(i), f"Dịch vụ số {i} mô tả dài để đẩy kích thước", str(100000 + i)] for i in range(40)]
    rows += [["", "", ""], ["Dịch vụ triệt lông", "", ""], ["STT", "Vùng triệt", "Giá buổi lẻ"]]
    rows += [["1", "Mép", "129000"], ["2", "Mặt", "249000"]]
    md = rows_to_structured_markdown(rows)
    chunks = smart_chunk(md)
    ents = {e.name: e for e in parse_table_chunks([{"content": c} for c in chunks])}
    assert "Mép" in ents, "zone row lost across chunk boundary"
    assert ents["Mép"].category == "Dịch vụ triệt lông", (
        "section heading was not re-attached → category lost across the split"
    )
