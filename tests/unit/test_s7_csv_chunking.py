"""CSV chunking fix: _is_table_line() CSV detection + profile.

Evidence: before fix, `_is_table_line("Tên dịch vụ,Mô tả,Giá")` returned
False, causing `analyze_document()` to report table_count=0 for pure CSV
input and forcing recursive-strategy fallback. After fix, CSV rows match
the new heuristic (≥ DEFAULT_CSV_MIN_COMMAS + no sentence punctuation).
"""
from __future__ import annotations

from ragbot.shared.chunking import _is_table_line, analyze_document


def test_csv_header_detected() -> None:
    assert _is_table_line("Tên dịch vụ,Mô tả,Giá") is True


def test_csv_row_detected() -> None:
    assert _is_table_line("Combo 10 buổi,Massage giảm stress,2399000") is True


def test_sentence_with_commas_not_table() -> None:
    # Guard: ordinary sentences with commas must NOT match CSV heuristic.
    assert _is_table_line("I love A, B, and C.") is False


def test_pipe_table_still_detected() -> None:
    # Regression guard for existing pipe-table path.
    assert _is_table_line("| col1 | col2 | col3 |") is True


def test_csv_doc_profile_table_count() -> None:
    csv = (
        "Tên,Mô tả,Giá\n"
        "Combo 10 buổi,Massage giảm stress,2399000\n"
        "Combo 5 buổi,Tẩy trang + đắp mặt nạ,1299000\n"
        "Gội đầu thư giãn,Gội + massage đầu,150000"
    )
    profile = analyze_document(csv)
    assert profile["table_count"] >= 1, f"CSV not detected: {profile}"
    assert profile["total_headings"] == 0
