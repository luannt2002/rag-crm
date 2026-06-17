"""VN legal clause prose must not be misclassified as a table row.

Root cause (P2-B 🐛-B): ``_is_table_line``'s CSV-comma rule
(``chunking.py`` CSV branch) fires on Vietnamese legal điểm-lines —
comma-rich, ending ';' not '.', no sentence-boundary ". " — so ~163/211
"TABLE"-labelled chunks in production were prose that got LLM-narrated and
embedded as a summary (paying an unneeded narrate hop and overriding the
``raw_only`` embed strategy). The classifier replayed on a real stored
chunk confirmed this.

Fix direction: the CSV rule must exclude VN list/clause markers
(``a)`` / ``(i)`` line starts) and clause-continuation endings (';' / ':').
Real pipe / numbered-price / true CSV rows still classify TABLE.
"""

from __future__ import annotations

from ragbot.application.services.narrate_dispatch import classify_chunk_block_type
from ragbot.shared.chunking import _is_table_line

VN_LEGAL_CLAUSE = (
    "a) Chia tách thành các vùng mạng khác nhau theo đối tượng sử dụng, "
    "mục đích sử dụng và hệ thống thông tin, tối thiểu: (i) phân vùng;"
)


def test_legal_clause_line_is_not_a_table_row() -> None:
    assert _is_table_line(VN_LEGAL_CLAUSE) is False


def test_roman_subpoint_clause_is_not_a_table_row() -> None:
    assert _is_table_line(
        "(i) phân vùng mạng nội bộ, mạng quản lý, mạng dịch vụ;",
    ) is False


def test_clause_ending_with_colon_is_not_a_table_row() -> None:
    assert _is_table_line(
        "Tổ chức thực hiện quản lý an toàn, bảo mật, như sau:",
    ) is False


def test_legal_article_chunk_classifies_text_not_table() -> None:
    chunk = (
        "[Chương 2 > Mục 4 > Điều 23. Quản lý an toàn]\n"
        "Tổ chức thực hiện quản lý an toàn, bảo mật hệ thống mạng như sau:\n"
        + VN_LEGAL_CLAUSE + "\n"
        "b) Có thiết bị có chức năng tường lửa để kiểm soát các kết nối, "
        "truy cập vào ra các vùng mạng quan trọng;\n"
    )
    assert classify_chunk_block_type(chunk) == "TEXT"


# ── Regression guards: real tabular content MUST still classify TABLE ──

def test_real_pipe_table_still_classifies_table() -> None:
    table = "| STT | Dịch vụ | Giá |\n|---|---|---|\n| 1 | Gội đầu | 60,000đ |"
    assert classify_chunk_block_type(table) == "TABLE"


def test_numbered_price_row_still_table() -> None:
    assert _is_table_line("1 | Gội đầu | 60,000đ") is True


def test_plain_csv_row_still_table() -> None:
    # A genuine CSV data row: no clause marker, no ';'/':' terminator.
    assert _is_table_line("Gội đầu, Massage body, Chăm sóc da, 250000") is True
