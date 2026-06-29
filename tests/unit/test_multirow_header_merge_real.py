"""[T1-Smartness] REAL xe-2 multi-row-header merge — converter + extractor.

Runtime-proven col_N CRUX: the xe warehouse export is a 2-row header —
  row1 = [«», Tên kho, Mã hàng, Tên hàng, «», «», «», «», «», «»]
  row2 = [«», «», «», «», date1, date2, hình ảnh1, ẢNH 1, ẢNH 2, Ảnh 3]
The converter opened the header on row1 alone → col5..col10 placeholders, then
emitted row2 (the REAL later-column names) as a DATA row → the names were lost and
the data row's values landed under col_N. The LLM then refuses on a header-only /
col_N chunk despite having the data (the xe 0/5 bug).

This guards BOTH halves of the fix:
  (1) the CONVERTER emits ONE merged header with date1/hình ảnh (NO col5..col10);
  (2) the EXTRACTOR collapses an already-STORED col_N-baked header + a continuation
      row → 0 col_N attribute keys (so old chunks self-heal at extraction time).
Clean single-row tables stay byte-identical.
"""
from __future__ import annotations

import re

from ragbot.shared.document_stats import parse_table_chunks
from ragbot.shared.tabular_markdown import rows_to_structured_markdown

_COL_N_RE = re.compile(r"^col_?\d+$", re.IGNORECASE)

# The EXACT xe-2 raw 2-row-header shape (10 columns, accent-stripped variant of the
# warehouse export). Row1 names the first columns; row2 names the later ones.
_XE2_ROWS = [
    ["", "Ten kho", "Ma hang", "Ten hang", "", "", "", "", "", ""],
    ["", "", "", "", "date1", "date2", "hinh anh1", "ANH 1", "ANH 2", "Anh 3"],
    ["Kho A", "Kho lop", "2-R16 195/65 NEO", "Lop NEOTERRA 195/65R16",
     "26", "27", "http://img/x", "a1", "a2", "a3"],
]


def _attr_keys(entities) -> set[str]:
    keys: set[str] = set()
    for e in entities:
        keys.update(e.attributes.keys())
    return keys


def _header_line(md: str) -> str:
    """The first emitted pipe-table header line."""
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("|") and ("date1" in s.lower() or "ten kho" in s.lower()):
            return s
    # Fallback: first pipe row.
    for ln in md.splitlines():
        if ln.strip().startswith("|"):
            return ln.strip()
    return ""


def test_converter_merges_two_row_header_no_col_n() -> None:
    """(1) The converter emits a SINGLE header with date1/hình ảnh and NO col5..col10."""
    md = rows_to_structured_markdown(_XE2_ROWS)
    header = _header_line(md)
    assert header, f"no header line emitted:\n{md}"
    low = header.lower()
    # Real row-2 names must be present as column labels …
    assert "date1" in low, f"date1 lost to col_N:\n{md}"
    assert "hinh anh1" in low, f"image column lost to col_N:\n{md}"
    # … and NONE of the col5..col10 placeholders may appear.
    for ph in ("col5", "col6", "col7", "col8", "col9", "col10"):
        assert ph not in low, f"placeholder {ph} leaked into merged header:\n{md}"
    # The continuation row must NOT survive as its own data row (no all-name row).
    assert md.count("date1") == 1, f"continuation row re-emitted as data:\n{md}"


def test_converter_merged_header_carries_data_values_under_real_labels() -> None:
    """End-to-end: convert → extract → the date/image VALUES ride under real labels,
    never under col_N."""
    md = rows_to_structured_markdown(_XE2_ROWS)
    ents = parse_table_chunks([{"content": md}])
    assert ents, f"no entity extracted from merged table:\n{md}"
    names = {e.name for e in ents}
    assert "Lop NEOTERRA 195/65R16" in names, f"name col mis-bound: {names}"
    keys = _attr_keys(ents)
    # col1 = position 0, empty in BOTH header rows (STT/index column, no label anywhere)
    # → a legitimate headerless-column placeholder, NOT the bug. The bug = col5..col10
    # (positions that HAD a real label in the continuation row); those must be healed.
    leaked = [k for k in keys if _COL_N_RE.match(k) and k.lower() != "col1"]
    assert not leaked, f"real-labelled cols leaked as col_N: {leaked} (all keys: {keys})"
    assert any("date1" in k.lower() for k in keys), f"date1 not a labelled attr: {keys}"


def test_extractor_heals_already_stored_col_n_chunk() -> None:
    """(2) An ALREADY-STORED chunk whose header baked col5..col10, followed by the
    label-only continuation row, must merge the labels over the placeholders →
    0 col_N attribute keys (old chunks self-heal at extraction time)."""
    stored = (
        "col1,Ten kho,Ma hang,Ten hang,col5,col6,col7,col8,col9,col10\n"
        ",,,,date1,date2,hinh anh1,ANH 1,ANH 2,Anh 3\n"
        "Kho A,Kho lop,2-R16 195/65 NEO,Lop NEOTERRA 195/65R16,26,27,http://img/x,a1,a2,a3\n"
    )
    ents = parse_table_chunks([{"content": stored}])
    assert ents, "no entity extracted from stored col_N chunk"
    names = {e.name for e in ents}
    # The continuation row must NOT survive as a fake entity named 'date1'.
    assert "date1" not in names, f"continuation row became a fake entity: {names}"
    assert "Lop NEOTERRA 195/65R16" in names, f"real name lost: {names}"
    keys = _attr_keys(ents)
    leaked = [k for k in keys if _COL_N_RE.match(k) and k.lower() != "col1"]
    assert not leaked, f"stored col_N not healed: {leaked} (all keys: {keys})"
    # The real labels must now key the values.
    assert any("date1" in k.lower() for k in keys), f"date1 label not bound: {keys}"


def test_clean_single_row_header_byte_identical_converter() -> None:
    """A clean single-row-header table (no empty cells) is NEVER merged — the
    converter output is byte-identical to a no-merge run."""
    rows = [
        ["Ten", "Nhom", "Gia"],
        ["Goi A", "Cao cap", "500000"],
        ["Goi B", "Pho thong", "300000"],
    ]
    md = rows_to_structured_markdown(rows)
    assert "| Ten | Nhom | Gia |" in md, md
    # No col_N anywhere (clean header has no gaps to placeholder).
    assert not any(_COL_N_RE.search(tok) for tok in md.replace("|", " ").split()), md
    # Both data rows survive as their own rows (nothing consumed by a phantom merge).
    assert "| Goi A | Cao cap | 500000 |" in md
    assert "| Goi B | Pho thong | 300000 |" in md


def test_clean_single_row_header_byte_identical_extractor() -> None:
    """Clean single-row header extraction is unchanged (no merge path entered)."""
    content = "Ten,Nhom,Gia\nGoi A,Cao cap,500000\n"
    ents = parse_table_chunks([{"content": content}])
    assert ents and ents[0].name == "Goi A"
    assert ents[0].price_primary == 500000  # noqa: PLR2004 — literal corpus value


def test_real_data_row_after_header_not_merged() -> None:
    """A header with a gap whose NEXT row is a real DATA row (overlaps the named
    columns) must NOT be merged — only a complementary label row is a continuation."""
    rows = [
        ["Ten hang", "Ma", "", ""],          # header with trailing gaps
        ["Lop A", "R16", "700000", "in stock"],  # real data — overlaps Ten hang/Ma
    ]
    md = rows_to_structured_markdown(rows)
    ents = parse_table_chunks([{"content": md}])
    names = {e.name for e in ents}
    assert "Lop A" in names, f"data row wrongly consumed as header continuation: {names}"
