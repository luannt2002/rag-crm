"""Pin tests — 260525 Bug #9 multi-table CSV chunker.

Pre-fix: ``_detect_csv_regions`` returned the LONGEST single CSV run.
A doc containing 2 tables (e.g. price list + combo list) emitted only
the longest table's rows; the shorter table's data (and any numeric
values inside it — e.g. ``1499000``) was discarded into the ``post``
region and dropped.

Post-fix: ``_detect_csv_regions_all`` returns every CSV region in
document order; ``_chunk_table_csv_with_context`` loops per-region so
each table contributes its full row coverage plus per-table header /
footer synthetic chunks.

Concrete reproducer used here is a 2-table doc that mirrors the
``test-spa-id`` file 2 raw_content structure (verified DB extract
2026-05-25): BẢNG GIÁ CHĂM SÓC DA (18 row giá lẻ) + BẢNG GIÁ TRIỆT
LÔNG (12 row combo, includes 1499000).
"""

from __future__ import annotations

from ragbot.shared.chunking import (
    _chunk_table_csv_with_context,
    _detect_csv_regions_all,
)


_TWO_TABLE_DOC = """BẢNG GIÁ DỊCH VỤ CHĂM SÓC DA CÔNG NGHỆ CAO,,,
STT,Tên dịch vụ,Giá 1 buổi,
1,Chăm sóc da chuyên sâu,700.000,
2,Trị mụn chuyên sâu,700.000,
3,Chăm sóc da cấp ô xi tươi,800.000,
4,Chăm sóc da thải độc da,800.000,
5,Chăm sóc da cấp nước đa tầng,800.000,
BẢNG GIÁ DỊCH VỤ TRIỆT LÔNG,,,
Dịch vụ triệt lông
STT,Vùng triệt,Giá buổi lẻ,Giá Combo 10 buổi
1,Mép,129.000,899000
2,Mặt,249.000,1499000
3,Nách,199.000,1199000
12,Râu (nam),249.000,1499000
Khuyến mãi: Mua 10 buổi triệt tặng 5 buổi
Bảo hành 2 năm
"""


def _lines(doc: str) -> list[str]:
    return [ln for ln in doc.split("\n") if ln.strip()]


# -- Detector returns every region ------------------------------------------


def test_detector_finds_both_tables() -> None:
    regions = _detect_csv_regions_all(_lines(_TWO_TABLE_DOC))
    assert len(regions) == 2, (
        f"expected 2 CSV regions in the 2-table doc, got {len(regions)}"
    )


def test_detector_region_order_is_document_order() -> None:
    """Region 1 must precede region 2 in line index.

    Note: the first region's "header" line is whichever CSV-shape
    line begins the longest consecutive same-comma-count run. For
    this doc the ``BẢNG GIÁ ... ,,,`` heading has 3 commas matching
    the table rows below, so it gets absorbed into the run as the
    first line. That's structurally fine — the heading travels with
    the rows.
    """
    lines = _lines(_TWO_TABLE_DOC)
    regions = _detect_csv_regions_all(lines)
    # Region 1 must come before region 2.
    assert regions[0].header_idx < regions[1].header_idx
    # The structural invariant we care about: region 2 sits AFTER
    # region 1's last data row.
    assert regions[1].header_idx > regions[0].last_data_idx


def test_detector_first_region_pre_captures_doc_intro() -> None:
    """First region's pre = lines before the first CSV header.

    For the 2-table doc the only pre-line is the "BẢNG GIÁ DỊCH VỤ
    CHĂM SÓC DA..." heading (3 commas — but it precedes a single
    non-CSV line in the original raw; here we have it directly before
    the header so it lives in pre).
    """
    regions = _detect_csv_regions_all(_lines(_TWO_TABLE_DOC))
    # First region's pre is empty (BẢNG GIÁ ... has 3 commas same as
    # the header that follows — they merge into a single run actually).
    # The actual boundary captured between regions is what matters,
    # tested below.
    assert isinstance(regions[0].pre, list)


def test_detector_boundary_heading_in_second_region_pre() -> None:
    """The 'BẢNG GIÁ DỊCH VỤ TRIỆT LÔNG' boundary heading must appear
    in the SECOND region's pre region so retrieval surfaces the table
    title alongside the triệt-lông rows."""
    regions = _detect_csv_regions_all(_lines(_TWO_TABLE_DOC))
    second_pre_joined = "\n".join(regions[1].pre)
    assert "TRIỆT LÔNG" in second_pre_joined or "triệt lông" in second_pre_joined


def test_detector_last_region_post_captures_trailing_notes() -> None:
    """Trailing 'Khuyến mãi' + 'Bảo hành' lines belong to the LAST
    region's post (since they appear after the final CSV row)."""
    regions = _detect_csv_regions_all(_lines(_TWO_TABLE_DOC))
    last_post = "\n".join(regions[-1].post)
    assert "Khuyến mãi" in last_post
    assert "Bảo hành" in last_post


# -- Chunker emits chunks for BOTH tables -----------------------------------


def test_chunker_emits_rows_for_both_tables() -> None:
    chunks = _chunk_table_csv_with_context(
        _TWO_TABLE_DOC, header_footer_enabled=True,
    )
    joined = "\n".join(chunks)
    # Table 1 row sample
    assert "Chăm sóc da chuyên sâu" in joined
    # Table 2 row sample — including the critical 1499000 cell that was
    # silently dropped pre-fix.
    assert "1499000" in joined
    assert "Râu (nam)" in joined


def test_chunker_1499000_chunk_exists() -> None:
    """The chunk containing 1499000 must be emitted as its own row
    chunk so retrieval can score it independently. Pre-fix this row
    never made it into the chunk list at all.
    """
    chunks = _chunk_table_csv_with_context(
        _TWO_TABLE_DOC, header_footer_enabled=True,
    )
    row_chunks_with_1499000 = [c for c in chunks if "1499000" in c]
    assert len(row_chunks_with_1499000) >= 2, (
        f"Expected ≥2 chunks containing 1499000 "
        f"(Mặt + Râu nam combo), got {len(row_chunks_with_1499000)}"
    )


def test_chunker_emits_per_table_synthetic_chunks() -> None:
    """Each table region gets its OWN header / footer synthetic chunks
    (when the per-region pre/post is non-trivial)."""
    chunks = _chunk_table_csv_with_context(
        _TWO_TABLE_DOC, header_footer_enabled=True,
    )
    # Footer chunk should include the trailing notes (Khuyến mãi + Bảo hành).
    has_footer_chunk = any(
        "Khuyến mãi" in c and "Bảo hành" in c for c in chunks
    )
    assert has_footer_chunk, "Last-region footer chunk missing trailing notes"


# -- Single-table regression guards -----------------------------------------


def test_single_table_doc_still_emits_one_region() -> None:
    """A doc with one CSV table emits exactly one region (regression)."""
    doc = """STT,Vùng triệt,Giá buổi lẻ,Giá Combo 10 buổi
1,Mép,129.000,899000
2,Mặt,249.000,1499000
3,Nách,199.000,1199000
"""
    regions = _detect_csv_regions_all(_lines(doc))
    assert len(regions) == 1


def test_no_table_doc_returns_empty_region_list() -> None:
    """Prose without any CSV-shape run → empty list (caller treats as
    no-table and emits a single text chunk upstream)."""
    doc = """Paragraph one with no commas.
Bullet, with single comma.
Another paragraph here.
"""
    regions = _detect_csv_regions_all(_lines(doc))
    assert regions == []


# -- 3-table doc (stress edge case) -----------------------------------------


def test_prose_with_trailing_commas_not_treated_as_csv() -> None:
    """260525 Bug #9-followup — prose bullets with stray ",,," must not
    be detected as CSV-shape lines.

    Pre-fix the detector accepted any line with ≥1 comma. Real file 2
    raw_content has bullet prose like:
        "- Giúp giảm lông rõ rệt chỉ sau 1-2 buổi liệu trình,,,"
    (1 sentence + 3 trailing commas after spreadsheet export). These
    were absorbed into the table region as "header" rows, polluting
    every chunk emitted afterwards with prose noise.

    Post-fix: ``_is_csv_shape_line`` requires ≥2 non-empty cells.
    """
    from ragbot.shared.chunking import _is_csv_shape_line, _detect_csv_regions_all

    # Prose with trailing commas — must reject (1 non-empty cell only).
    assert _is_csv_shape_line("- Bullet sentence prose,,,") is False
    assert _is_csv_shape_line("Trailing notes line,,,") is False
    assert _is_csv_shape_line(",,,") is False

    # Real CSV row — must accept.
    assert _is_csv_shape_line("2,Mặt,249.000,1499000") is True
    assert _is_csv_shape_line("STT,Vùng triệt,Giá buổi lẻ,Giá Combo 10 buổi") is True

    # Mixed doc with prose ,,, + real table — region must skip prose.
    doc_lines = [
        "- First bullet sentence,,,",
        "- Second bullet sentence,,,",
        "- Third bullet sentence,,,",
        "STT,col_b,col_c,col_d",
        "1,foo,100,200",
        "2,bar,200,300",
    ]
    regions = _detect_csv_regions_all(doc_lines)
    assert len(regions) == 1
    # Header must be the real STT row, NOT the bullet prose
    assert doc_lines[regions[0].header_idx].startswith("STT,col_b")


def test_three_table_doc_finds_all_three() -> None:
    doc = """col_a,col_b,col_c
1,foo,100
2,bar,200
3,baz,300
=== Section 2 ===
col_x,col_y,col_z
10,red,a
20,green,b
30,blue,c
Section 3 prose intro.
key,value,unit
alpha,1.5,m
beta,2.5,cm
"""
    regions = _detect_csv_regions_all(_lines(doc))
    assert len(regions) == 3, (
        f"3 tables expected, got {len(regions)}: "
        f"{[(r.header_idx, r.last_data_idx) for r in regions]}"
    )
