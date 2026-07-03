"""Step-3 (T012): positive-table-evidence gate — the stats extractor must not
mint entities from prose-shaped documents.

Root cause (truth-audit workflow Q4, SỰ THẬT): extraction is ungated — any line
with ≥1 comma is a table-row candidate; `_is_prose_row` only catches rows ending
in a sentence terminator, while PDF hard-wraps produce comma-carrying fragments
with no terminator (or ';'). Result: legal-prose corpora minted 100+ price-less
"entities" ('tổ chức', 'cấp độ 3', …) that are retrievable garbage.

Gate: mint a row-entity only with POSITIVE evidence — a parsed price, a
pipe/tab-delimited row, or a STRUCTURAL header (separator-backed / vocab
token-match; the `_is_shape_header` heuristic alone is NOT structural — it is
what promoted prose lines to pseudo-headers, residual gap B).

Fixtures = the EXACT raw chunks (shape-preserving, brand-scrubbed where needed)
that mint the residual garbage at current HEAD — captured from the live DB by
the empirical mint-scan. Positive controls prove pipe tables and vocab-header
CSVs keep extracting unchanged (incl. price-less date rows a delivery-sheet
needs). Domain-neutral: the gate keys on structure only.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks

# --- REAL minting raws (bot-123 legal prose; verified minting at HEAD) -------
_PROSE_LEGAL_A = (
    "[Chương II CÁC QUY ĐỊNH VỀ BẢO ĐẢM AN TOÀN THÔNG TIN Mục 1 QUẢN LÝ TÀI SẢN "
    "CÔNG NGHỆ THÔNG TIN > Điều 8. Quản lý tài sản thông tin]\n"
    "- Với mỗi hệ thống thông tin\n\n"
    "phải lập danh sách tài sản thông tin, quy định về thẩm quyền, trách nhiệm "
    "của cá nhân hoặc bộ phận của tổ chức được tiếp cận, khai thác và quản lý.\n\n"
    ", tổ chức\n\n"
    "- Tài sản thông tin phải phân loại theo\n\n"
    "quy định tại Điều 4 Thông tư này. loại thông tin\n\n"
    "- Tài sản thông tin thuộc loại thông tin bí mật phải được mã hóa hoặc có biện\n\n"
    "pháp bảo vệ để bảo mật thông tin trong quá trình tạo lập, trao đổi, lưu trữ.\n\n"
    "- Tài sản thông tin trên hệ thống thông tin\n\n"
    "cấp độ phải áp dụng phương án chống thất thoát dữ liệu. từ 3 trở lên"
)

_PROSE_LEGAL_B = (
    "[Chương I QUY ĐỊNH CHUNG > Điều 4. Phân loại thông tin]\n"
    "Thông tin xử lý, lưu trữ thông qua hệ thống thông tin được phân loại theo "
    "thuộc tính bí mật như sau:\n\n"
    "Thông tin công cộng là thông tin được công khai cho tất cả các đối tượng mà "
    "không cần xác định danh tính, địa chỉ cụ thể của các đối tượng đó;\n\n\n"
    "Thông tin là thông tin được phân quyền quản lý, khai thác cho một hoặc một "
    "nhóm đối tượng được xác định danh tính;\n\n"
    "- riêng (hoặc thông tin nội bộ)\n"
    "- Thông tin cá nhân là thông tin định danh khách hàng và các thông tin sau "
    "đây: thông tin về tài khoản, thông tin về tiền gửi, thông tin về tài sản "
    "gửi, thông tin về giao dịch và các thông tin có liên quan khác;\n\n"
    "Thông tin bí mật là: (i) hông tin Mật, Tối Mật, Tuyệt Mật theo quy định\n\n"
    "- T\n\n"
    "của pháp luật về bảo vệ bí mật nhà nước\n\n"
    "- ; (ii) Thông tin hạn chế tiếp cận theo quyđịnh của tổ chức\n\n"
    ".\n\n"
    "- 4"
)

# --- Positive controls (must KEEP extracting) --------------------------------
# Real delivery-sheet shape (2-row merged header + EMPTY first cell + price-less
# date rows): the gate must keep these rows — date questions need them.
_PIPE_DELIVERY_SHEET = (
    "| MARKS | CARGO DESCRIPTION | |\n\n"
    "| GR | SAMPLEBRAND TYRES | NGÀY VỀ |\n\n"
    "| | 185/55R16 83V SAMPLETRAXX G/P | 28-thg 11 |\n\n"
    "| | 195/65R15 91H SAMPLETRAXX G/P | 28-thg 11 |\n"
)

# Pipe row-atomic priced catalog (price-sheet shape).
_PIPE_PRICED = (
    "| question | code | productname | answer | quantity | price |\n"
    "| 185/55R15, 185 55 15 | 2-R15 185/55 AAA | Lốp mẫu 185/55R15 | MẪU 185/55R15 | 779 | 810000 |\n"
)

# Comma-CSV with a VOCAB header (gia/price token) — no pipes; must keep working
# (comma-catalog corpora exist; the header token-match is structural evidence).
_CSV_VOCAB_HEADER = (
    "ten,gia,so luong\n"
    "Dịch vụ mẫu A,150000,12\n"
    "Dịch vụ mẫu B,,5\n"
)


def _parse(text: str) -> list:
    return parse_table_chunks([{"id": "c1", "chunk_index": 0, "content": text}])


def test_legal_prose_mints_zero_entities_gap_a() -> None:
    """RED target: comma-carrying wrapped legal prose (no pipe, no structural
    header) must yield ZERO entities — at pre-gate HEAD it mints 'tổ chức'."""
    assert [e.name for e in _parse(_PROSE_LEGAL_A)] == []


def test_legal_prose_mints_zero_entities_gap_b() -> None:
    """Pre-gate HEAD mints 2 prose-sentence entities from this raw."""
    assert [e.name for e in _parse(_PROSE_LEGAL_B)] == []


def test_pipe_delivery_sheet_price_less_rows_kept() -> None:
    ents = _parse(_PIPE_DELIVERY_SHEET)
    names = [e.name for e in ents]
    assert any("185/55R16" in n for n in names), "price-less pipe rows must survive"
    assert any("195/65R15" in n for n in names)


def test_pipe_priced_catalog_kept() -> None:
    ents = _parse(_PIPE_PRICED)
    assert len(ents) == 1
    assert ents[0].price_primary == 810000


def test_csv_with_vocab_header_kept_even_price_less_row() -> None:
    ents = _parse(_CSV_VOCAB_HEADER)
    names = [e.name for e in ents]
    assert any("Dịch vụ mẫu A" in n for n in names)
    # The price-less row under a STRUCTURAL (vocab) header is still legitimate.
    assert any("Dịch vụ mẫu B" in n for n in names)
