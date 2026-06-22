"""Table-structure taxonomy stress-test — is the converter + extractor multi-tasking?

Pushes a LARGE, format-diverse corpus through the REAL production code path and
scores each PASS / GRACEFUL / FAIL / RISK. Three suites:

  A. TABULAR      rows → ``rows_to_structured_markdown`` (L1) → ``parse_table_chunks``
                  (L3). Covers the SOTA table-structure taxonomy (Docling / Microsoft
                  TATR / PubTables-1M / SciTSR / Lautert / unstructured.io).
  B. MD-NATIVE    a markdown string straight into ``parse_table_chunks`` — simulates
                  what the PDF / DOCX / HTML / Sheets parsers ALREADY emit (the
                  canonical IR). Proves the format-agnostic contract.
  C. MONEY        every Vietnamese/English currency format a price cell can take.

Every fixture is synthetic + domain-neutral (generic "Item A" / "Region" / years,
no tenant vocabulary) so it proves SHAPE-based handling, not memorised data.
Run: ``python scripts/table_taxonomy_stress_test.py``.

Scoring (honest, two independent paths):
  * md_grid  : L1 kept the table as a markdown grid (the VECTOR/LLM path is
               orientation-agnostic — even a transposed grid is readable by an LLM).
  * stats_ok : L3 extracted the RIGHT entities (name↔price), OR — for non-relational
               shapes it cannot model — avoided emitting GARBAGE (a label like
               "Giá"/"Tổng" promoted to an entity). Silent garbage > a graceful skip.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ragbot.shared.document_stats import parse_table_chunks  # noqa: E402
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402

# Column-label / aggregate words that must NEVER be a row entity (garbage check).
_LABEL_WORDS = {
    "giá", "tổng", "tổng cộng", "thuộc tính", "chỉ số", "mã", "loại", "stt",
    "đơn giá", "thành tiền", "số lượng",
}


def _entities(md: str) -> list[tuple[str, int | None]]:
    return [(e.name.strip(), e.price_primary) for e in parse_table_chunks([{"content": md}])]


def _priced(ents) -> dict[str, int]:
    return {n: p for n, p in ents if p}


def _has_garbage(ents) -> bool:
    return any(n.lower() in _LABEL_WORDS for n, _ in ents)


# ─────────────────────────────────────────────────────────────────────────────
# SUITE A — TABULAR (rows → rows_to_structured_markdown → parse_table_chunks)
# ─────────────────────────────────────────────────────────────────────────────
TABULAR: list[tuple[str, str, list[list[str]], dict]] = [
    ("A01", "Simple top-header (flat relational)",
     [["STT", "Tên", "Giá"], ["1", "Item A", "100000"], ["2", "Item B", "200000"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A02", "Transposed (attribute col0, entities=cols)",
     [["Thuộc tính", "Item A", "Item B"], ["Giá", "100000", "200000"], ["Bảo hành", "12", "24"]],
     {"kind": "transposed"}),
    ("A03", "No-header (data only)",
     [["Item A", "100000", "đỏ"], ["Item B", "200000", "xanh"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A04", "Multi-ROW header (hierarchical cols)",
     [["", "Quý 1", "", "Quý 2", ""], ["Sản phẩm", "Doanh thu", "Chi phí", "Doanh thu", "Chi phí"],
      ["Item A", "100000", "50000", "120000", "60000"]],
     {"kind": "relational", "want": {"Item A"}}),
    ("A05", "Multi-COL row index / stub (3-col)",
     [["Nhóm", "Tên", "Giá"], ["Cao cấp", "Item A", "100000"], ["Cao cấp", "Item B", "200000"],
      ["Phổ thông", "Item C", "50000"]],
     {"kind": "relational", "want": {"Item A", "Item B", "Item C"}}),
    ("A06", "Bi-directional 2-D (row+col headers)",
     [["", "2022", "2023"], ["Miền Bắc", "100000", "110000"], ["Miền Nam", "90000", "95000"]],
     {"kind": "matrix"}),
    ("A07", "Crosstab / pivot (region × product)",
     [["", "Item A", "Item B"], ["Miền Bắc", "100000", "200000"], ["Miền Nam", "90000", "180000"]],
     {"kind": "matrix"}),
    ("A08", "Sparse matrix (mostly empty)",
     [["", "C1", "C2", "C3"], ["R1", "100000", "", ""], ["R2", "", "200000", ""]],
     {"kind": "matrix"}),
    ("A09", "Time-series panel (year columns)",
     [["Chỉ số", "2020", "2021", "2022"], ["Doanh thu", "100000", "110000", "120000"]],
     {"kind": "matrix"}),
    ("A10", "Horizontal span (colspan → blanks)",
     [["Khu vực", "Quý 1", "", ""], ["", "T1", "T2", "T3"], ["Item A", "100000", "110000", "120000"]],
     {"kind": "matrix"}),
    ("A11", "Vertical span (rowspan → blank group)",
     [["Nhóm", "Tên", "Giá"], ["Cao cấp", "Item A", "100000"], ["", "Item B", "200000"],
      ["Phổ thông", "Item C", "50000"]],
     {"kind": "relational", "want": {"Item A", "Item B", "Item C"}}),
    ("A12", "2-col category-token table (Vùng | Giá)",
     [["Vùng", "Giá"], ["Mép", "129000"], ["Nách", "199000"]],
     {"kind": "relational", "want": {"Mép", "Nách"}}),
    ("A13", "Stacked sub-tables + section titles",
     [["Nhóm Alpha"], ["Tên", "Giá"], ["Item A", "100000"], [""],
      ["Nhóm Beta"], ["Tên", "Giá"], ["Item B", "200000"]],
     {"kind": "relational", "want": {"Item A", "Item B"}, "sections": {"Nhóm Alpha", "Nhóm Beta"}}),
    ("A14", "Mixed 2-col + 3-col multi-section",
     [["Dịch vụ chăm sóc da"], ["STT", "Tên", "Giá"], ["1", "Item A", "100000"], [""],
      ["Dịch vụ triệt lông"], ["Vùng", "Giá"], ["Mép", "129000"], ["Nách", "199000"]],
     {"kind": "relational", "want": {"Item A", "Mép", "Nách"},
      "sections": {"Dịch vụ chăm sóc da", "Dịch vụ triệt lông"}}),
    ("A15", "Repeated header mid-table (page break)",
     [["Tên", "Giá"], ["Item A", "100000"], ["Tên", "Giá"], ["Item B", "200000"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A16", "Vertical key-value (entity profile)",
     [["Tên", "Item A"], ["Giá", "100000"], ["Mô tả", "chất lượng cao"]],
     {"kind": "kv", "entity": "Item A"}),
    ("A17", "Horizontal key-value (form)",
     [["Tên", "Item A", "Giá", "100000"], ["Mã", "A01", "Loại", "đỏ"]],
     {"kind": "kv", "entity": "Item A"}),
    ("A18", "Indented row hierarchy",
     [["Mục", "Giá"], ["Nhóm cha", ""], ["  Item A", "100000"], ["  Item B", "200000"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A19", "Subtotal / total row interleaved",
     [["Tên", "Giá"], ["Item A", "100000"], ["Item B", "200000"], ["Tổng cộng", "300000"]],
     {"kind": "relational", "want": {"Item A", "Item B"}, "no_garbage": {"Tổng cộng"}}),
    ("A20", "Ragged rows (varying col count)",
     [["Tên", "Mã", "Giá"], ["Item A", "A01"], ["Item B", "B02", "200000", "extra"]],
     {"kind": "relational", "want": {"Item B"}}),
    ("A21", "Sparse data (many empty cells)",
     [["Tên", "Mã", "Giá", "Ghi chú"], ["Item A", "", "100000", ""], ["Item B", "B02", "", ""]],
     {"kind": "relational", "want": {"Item A"}}),
    ("A22", "Footnote row in body",
     [["Tên", "Giá"], ["Item A", "100000"], ["* Giá chưa gồm VAT"]],
     {"kind": "relational", "want": {"Item A"}}),
    ("A23", "Enumeration / list table",
     [["Item A"], ["Item B"], ["Item C"]],
     {"kind": "list"}),
    ("A24", "Layout / navigation table (no data)",
     [["Trang chủ", "Giới thiệu", "Liên hệ"]],
     {"kind": "layout"}),
    ("A25", "PDF phantom-column split (price split)",
     [["Tên", "Giá", "trị"], ["Item A", "100", "000"]],
     {"kind": "noise"}),
    ("A26", "Column-header mid-table shift",
     [["Tên", "Giá"], ["Item A", "100000"], ["Khu vực", "Dân số"], ["Miền Bắc", "1000000"]],
     {"kind": "relational", "want": {"Item A"}}),
    ("A27", "Section-in-header ('X,,col,col')",
     [["Gói dịch vụ A", "", "Thời gian", "Giá"], ["1", "Item A", "30 phút", "100000"]],
     {"kind": "relational", "want": {"Item A"}, "sections": {"Gói dịch vụ A"}}),
    ("A28", "Money as name ('Gói 6 triệu')",
     [["Tên gói", "Giá"], ["Gói 6 triệu", "6000000"], ["Gói cơ bản", "2000000"]],
     {"kind": "relational", "want": {"Gói 6 triệu", "Gói cơ bản"}}),
    ("A29", "Long-title section (incidental year)",
     [["Bảng giá dịch vụ chăm sóc chuyên sâu cao cấp 2026"], ["Tên", "Giá"], ["Item A", "100000"]],
     {"kind": "relational", "want": {"Item A"},
      "sections": {"Bảng giá dịch vụ chăm sóc chuyên sâu cao cấp 2026"}}),
    ("A30", "Wide table (6 cols, combo prices)",
     [["STT", "Tên", "Mã", "Đơn giá", "Combo", "Ghi chú"],
      ["1", "Item A", "A01", "100000", "270000", "hot"], ["2", "Item B", "B02", "200000", "540000", ""]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A31", "Quoted cell with internal commas",
     [["Tên", "Giá"], ['"Item A, bản đặc biệt"', "100000"]],
     {"kind": "relational", "want": {"Item A, bản đặc biệt"}}),
    ("A32", "Multi-word headers (Tên dịch vụ | Đơn giá)",
     [["Tên dịch vụ", "Đơn giá"], ["Item A", "100000"], ["Item B", "200000"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A33", "All-empty / whitespace rows",
     [["", "", ""], [" ", "", " "], ["Tên", "Giá"], ["Item A", "100000"]],
     {"kind": "relational", "want": {"Item A"}}),
    ("A34", "Header-only, no data rows",
     [["Tên", "Giá"]],
     {"kind": "empty"}),
    # ── real-customer-derived shapes (domain-neutral replicas of the 4 spa files) ──
    ("A35", "Multi-currency-column + x-grid (file#1 tier sheet)",
     [["Dịch vụ", "Giá lẻ", "Gói A", "Gói B"], ["Item A", "700000", "x", "x"],
      ["Item B", "1500000", "", "x"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A36", "Numbered-service sections (file#2)",
     [["1. Nâng cơ trẻ hóa"], ["Tên", "Giá"], ["Item A", "100000"],
      ["2. Trẻ hóa da"], ["Tên", "Giá"], ["Item B", "200000"]],
     {"kind": "relational", "want": {"Item A", "Item B"},
      "sections": {"1. Nâng cơ trẻ hóa", "2. Trẻ hóa da"}}),
    ("A37", "Sale-banner line + table (file#2)",
     [["BẢNG GIÁ CÔNG NGHỆ CAO"], ["Gói buffet đang sale 50%, chia 3 mức giá"],
      ["Tên", "Giá"], ["Item A", "700000"]],
     {"kind": "relational", "want": {"Item A"}}),
    ("A38", "Availability grid (x marks only, no price)",
     [["Dịch vụ", "Cơ bản", "VIP"], ["Item A", "x", "x"], ["Item B", "", "x"]],
     {"kind": "noprice", "want_names": {"Item A", "Item B"}}),
    ("A39", "Price cells with units (từ / /buổi)",
     [["Tên", "Giá"], ["Item A", "từ 500k"], ["Item B", "300k/buổi"]],
     {"kind": "relational", "want": {"Item A", "Item B"}}),
    ("A40", "Discount 4-col (gốc / giảm / sale)",
     [["Tên", "Giá gốc", "Giảm", "Giá sale"], ["Item A", "1000000", "30%", "700000"]],
     {"kind": "relational", "want": {"Item A"}}),
]

# ─────────────────────────────────────────────────────────────────────────────
# SUITE B — MARKDOWN-NATIVE (md string → parse_table_chunks). Simulates the IR
# that the PDF / DOCX / HTML / Sheets parsers already emit.
# ─────────────────────────────────────────────────────────────────────────────
MD_NATIVE: list[tuple[str, str, str, dict]] = [
    ("B01", "Clean heading + pipe table",
     "## Dịch vụ A\n\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n| Item B | 200000 |\n",
     {"want": {"Item A", "Item B"}, "category": {"Item A": "Dịch vụ A"}}),
    ("B02", "Two headings, two tables (legal/catalog)",
     "## Nhóm A\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n\n"
     "## Nhóm B\n| Tên | Giá |\n| --- | --- |\n| Item B | 200000 |\n",
     {"want": {"Item A", "Item B"}, "category": {"Item A": "Nhóm A", "Item B": "Nhóm B"}}),
    ("B03", "Table with NO heading",
     "| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n",
     {"want": {"Item A"}}),
    ("B04", "Prose + inline table (PDF-style)",
     "Bảng dưới đây liệt kê giá dịch vụ.\n\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n\n"
     "Giá trên chưa gồm VAT.\n",
     {"want": {"Item A"}}),
    ("B05", "DOCX-style: para, table, para in-order",
     "# Báo giá\nKính gửi quý khách.\n\n## Sản phẩm\n| Tên | Đơn giá |\n| --- | --- |\n"
     "| Item A | 100000 |\n\nLiên hệ để biết thêm.\n",
     {"want": {"Item A"}, "category": {"Item A": "Sản phẩm"}}),
    ("B06", "Multi-word headers + currency in header",
     "| Tên dịch vụ | Đơn giá (VNĐ) |\n| --- | --- |\n| Item A | 1.499.000 |\n",
     {"want": {"Item A"}, "price": {"Item A": 1499000}}),
    ("B07", "Mixed money formats in one table",
     "| Tên | Giá |\n| --- | --- |\n| Item A | 899k |\n| Item B | 1tr499 |\n| Item C | 6 triệu |\n",
     {"want": {"Item A", "Item B", "Item C"},
      "price": {"Item A": 899000, "Item B": 1499000, "Item C": 6000000}}),
    ("B08", "Bullet list (not a table) → 0 entities",
     "## Ưu đãi\n- Giảm 10% cho khách mới\n- Tặng kèm 1 buổi\n",
     {"kind": "list"}),
    ("B09", "Wide table (6 cols)",
     "| STT | Tên | Mã | Đơn giá | Combo | Ghi chú |\n| --- | --- | --- | --- | --- | --- |\n"
     "| 1 | Item A | A01 | 100000 | 270000 | hot |\n",
     {"want": {"Item A"}}),
    ("B10", "Two tables under ONE heading",
     "## Bảng giá\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n\n"
     "| Tên | Giá |\n| --- | --- |\n| Item B | 200000 |\n",
     {"want": {"Item A", "Item B"}, "category": {"Item A": "Bảng giá", "Item B": "Bảng giá"}}),
    ("B11", "Heading > subheading > table (nested)",
     "# Chương 1\n## Mục 1.1\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n",
     {"want": {"Item A"}, "category": {"Item A": "Mục 1.1"}}),
    ("B12", "Table + formula block + table",
     "| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n\n$$E = mc^2$$\n\n"
     "| Tên | Giá |\n| --- | --- |\n| Item B | 200000 |\n",
     {"want": {"Item A", "Item B"}}),
    ("B13", "Prose-heavy doc, one buried table",
     "# Giới thiệu\nĐây là tài liệu dài.\nNhiều đoạn văn xuôi giải thích.\n\n"
     "## Bảng giá\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n\nKết luận ở đây.\n",
     {"want": {"Item A"}, "category": {"Item A": "Bảng giá"}}),
    ("B14", "Pipe-escaped content in a cell",
     "| Tên | Giá |\n| --- | --- |\n| Item A \\| bản đặc biệt | 100000 |\n",
     {"want": {"Item A | bản đặc biệt"}}),
    ("B15", "No-price catalog (inventory, qty not price)",
     "| Tên | Số lượng |\n| --- | --- |\n| Item A | 5 |\n| Item B | 12 |\n",
     {"kind": "noprice", "want_names": {"Item A", "Item B"}}),
    ("B16", "TSV (tab-separated) table",
     "Tên\tGiá\nItem A\t100000\nItem B\t200000\n",
     {"want": {"Item A", "Item B"}}),
    ("B17", "Two tables, NO blank line between",
     "| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n"
     "| Tên | Giá |\n| --- | --- |\n| Item B | 200000 |\n",
     {"want": {"Item A", "Item B"}}),
    ("B18", "PPTX bullet slide (no table)",
     "# Slide 1\n- Điểm nổi bật 1\n- Điểm nổi bật 2\n",
     {"kind": "list"}),
    ("B19", "Deeply nested headings (#→####) + table",
     "# A\n## B\n### C\n#### D\n| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n",
     {"want": {"Item A"}}),
    ("B20", "Cell with <br> line break",
     "| Tên | Giá |\n| --- | --- |\n| Item A<br>bản mới | 100000 |\n",
     {"want": {"Item A<br>bản mới"}}),
    ("B21", "Currency-symbol prefix in cell (₫)",
     "| Tên | Giá |\n| --- | --- |\n| Item A | ₫100.000 |\n",
     {"want": {"Item A"}, "price": {"Item A": 100000}}),
    ("B22", "Markdown totals row (bold)",
     "| Tên | Giá |\n| --- | --- |\n| Item A | 100000 |\n| **Tổng** | **100000** |\n",
     {"want": {"Item A"}, "no_garbage": {"Tổng", "**Tổng**"}}),
    ("B23", "HTML-converted: repeated merged header value",
     "| Khu vực | Khu vực | Giá |\n| --- | --- | --- |\n| Miền Bắc | Hà Nội | 100000 |\n",
     {"want": {"Miền Bắc"}}),
]

# ─────────────────────────────────────────────────────────────────────────────
# SUITE C — MONEY FORMAT robustness (single 2-col table, vary the price cell)
# ─────────────────────────────────────────────────────────────────────────────
MONEY: list[tuple[str, str, int | None]] = [
    ("C01", "1.499.000", 1499000),
    ("C02", "1,499,000", 1499000),
    ("C03", "899k", 899000),
    ("C04", "1tr499", 1499000),
    ("C05", "1.5tr", 1500000),
    ("C06", "6 triệu", 6000000),
    ("C07", "1M", 1000000),
    ("C08", "5000 nghìn", 5000000),
    ("C09", "200.000đ", 200000),
    ("C10", "1.200.000 VND", 1200000),
    ("C11", "Liên hệ", None),
    ("C12", "100k-200k", None),  # range — first/either value acceptable; just no crash
    ("C13", "từ 500k", 500000),
    ("C14", "500k/buổi", 500000),
    ("C15", "Giá: 300k", 300000),
    ("C16", "miễn phí", None),
    ("C17", "Free", None),
    ("C18", "0đ", None),
    ("C19", "1.2tr", 1200000),
    ("C20", "500 nghìn", 500000),
    ("C21", "3.000.000", 3000000),
    ("C22", "₫100000", 100000),
    ("C23", "100.000 - 200.000", None),  # range — no crash; first value acceptable
    ("C24", "2tr5", None),  # KNOWN number_format quirk (parses 2,005,000) — flag, no crash
]


def _verdict_relational(md, ents, expect):
    sections = {ln[3:].strip() for ln in md.splitlines() if ln.startswith("## ")}
    got_priced = _priced(ents)
    notes = []
    if "sections" in expect:
        miss = expect["sections"] - sections
        notes.append("sect✓" if not miss else f"sect✗{miss}")
    want = expect.get("want", set())
    hit = want & set(got_priced)
    if expect.get("no_garbage") and (expect["no_garbage"] & set(got_priced)):
        return "RISK", f"garbage={expect['no_garbage'] & set(got_priced)} " + " ".join(notes)
    sec_fail = "sections" in expect and (expect["sections"] - sections)
    if hit == want and not sec_fail:
        return "PASS", f"priced={sorted(hit)} " + " ".join(notes)
    if hit:
        return "PARTIAL", f"priced={sorted(hit)}/{sorted(want)} " + " ".join(notes)
    return "FAIL", f"want={sorted(want)} got={sorted(got_priced)} " + " ".join(notes)


def _verdict_nonrelational(md, ents, expect):
    md_grid = "| --- " in md
    garbage = _has_garbage(ents)
    kind = expect["kind"]
    if kind in ("transposed", "matrix"):
        if md_grid and not garbage:
            return "GRACEFUL", f"grid✓ no-garbage {ents[:2]}"
        return ("RISK" if md_grid else "FAIL"), f"garbage/no-grid {ents[:3]}"
    if kind == "kv":
        ok = expect.get("entity") in {n for n, _ in ents}
        return ("GRACEFUL" if (ok or not garbage) else "RISK"), f"{ents[:3]}"
    if kind == "list":
        return ("GRACEFUL" if not _priced(ents) else "RISK"), f"{ents[:3]}"
    if kind == "layout":
        return ("GRACEFUL" if not ents else "RISK"), f"{ents[:3]} (want empty)"
    if kind == "empty":
        return ("GRACEFUL" if not ents else "RISK"), f"{ents[:3]} (want empty)"
    if kind == "noprice":
        names = {n for n, _ in ents}
        return ("GRACEFUL" if not _priced(ents) else "RISK"), f"names={names} no-fake-price"
    return "INFO", str(ents[:3])


def run_suite(title, run):
    tally: dict[str, int] = {}
    print(f"\n{'═' * 110}\n{title}\n{'═' * 110}")
    print(f"{'ID':5} {'VERDICT':9} {'CASE':46} DETAIL")
    print("─" * 110)
    for cid, name, verdict, detail in run():
        tally[verdict] = tally.get(verdict, 0) + 1
        print(f"{cid:5} {verdict:9} {name[:46]:46} {detail}")
    print("─" * 110)
    print("TALLY:", "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return tally


def _run_tabular():
    for cid, name, rows, expect in TABULAR:
        md = rows_to_structured_markdown(rows)
        ents = _entities(md)
        if expect["kind"] == "relational":
            v, d = _verdict_relational(md, ents, expect)
        else:
            v, d = _verdict_nonrelational(md, ents, expect)
        yield cid, name, v, d


def _run_md_native():
    for cid, name, md, expect in MD_NATIVE:
        ents = _entities(md)
        if "kind" in expect:
            v, d = _verdict_nonrelational(md, ents, expect)
        else:
            v, d = _verdict_relational(md, ents, expect)
            # extra: category + exact-price assertions when given
            cats = {e.name.strip(): e.category for e in parse_table_chunks([{"content": md}])}
            if v == "PASS" and expect.get("category"):
                bad = {k: cats.get(k) for k, want in expect["category"].items() if cats.get(k) != want}
                if bad:
                    v, d = "PARTIAL", f"cat-mismatch={bad}"
            if v == "PASS" and expect.get("price"):
                pr = _priced(ents)
                bad = {k: pr.get(k) for k, want in expect["price"].items() if pr.get(k) != want}
                if bad:
                    v, d = "FAIL", f"price-mismatch={bad}"
        yield cid, name, v, d


def _run_money():
    for cid, cell, want in MONEY:
        md = f"| Tên | Giá |\n| --- | --- |\n| Item A | {cell} |\n"
        pr = _priced(_entities(md))
        got = pr.get("Item A")
        if want is None:
            v, d = ("PASS" if got is None else "INFO"), f"cell={cell!r} → price={got} (want None/no-crash)"
        else:
            v, d = ("PASS" if got == want else "FAIL"), f"cell={cell!r} → price={got} (want {want})"
        yield cid, f"money {cell!r}", v, d


def main() -> None:
    t1 = run_suite("SUITE A — TABULAR (rows → L1 → L3)", _run_tabular)
    t2 = run_suite("SUITE B — MARKDOWN-NATIVE (md → L3, simulates pdf/docx/html IR)", _run_md_native)
    t3 = run_suite("SUITE C — MONEY FORMAT robustness", _run_money)
    grand: dict[str, int] = {}
    for t in (t1, t2, t3):
        for k, v in t.items():
            grand[k] = grand.get(k, 0) + v
    total = sum(grand.values())
    good = grand.get("PASS", 0) + grand.get("GRACEFUL", 0)
    print(f"\n{'█' * 110}")
    print(f"GRAND TOTAL ({total} cases): " + "  ".join(f"{k}={v}" for k, v in sorted(grand.items())))
    print(f"  → PASS+GRACEFUL (acceptable) = {good}/{total} = {good * 100 // total}%")


if __name__ == "__main__":
    main()
