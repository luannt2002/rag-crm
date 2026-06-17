"""_chunk_table_csv row-as-chunk strategy + select_strategy CSV pick.

Audit 2026-04-29 found service-name + price being split across chunks because
``select_strategy`` was falling back to ``recursive`` for a pure CSV
spreadsheet export. Row-as-chunk keeps each (service, price, ...) tuple
atomic — these tests guard the contract end-to-end.
"""
from __future__ import annotations

from ragbot.shared.chunking import (
    _chunk_table_csv,
    _is_csv_format,
    analyze_document,
    select_strategy,
    smart_chunk,
)
from ragbot.shared.constants import DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS


_CSV_DOC = (
    "Tên dịch vụ,Mô tả,Giá (VND)\n"
    "Gội đầu thường,Gội đầu massage 30 phút,150000\n"
    "Chăm sóc da mặt,Tẩy trang + đắp mặt nạ,1299000\n"
    "Triệt lông toàn thân,Gói 6 buổi laser,4999000\n"
    "Trẻ hóa da bằng HIFU,Gói 3 buổi,8999000\n"
    "Căng bóng da Mesotherapy,Gói 5 buổi,3999000"
)


def test_csv_header_detected() -> None:
    """``_is_csv_format`` flips True for a multi-row CSV with no sentence punctuation."""
    assert _is_csv_format(_CSV_DOC) is True


def test_each_row_becomes_chunk() -> None:
    """One data row = one chunk. Header excluded from chunk count."""
    chunks = _chunk_table_csv(_CSV_DOC)
    # 5 data rows in fixture → 5 chunks (header is prepended, not counted).
    assert len(chunks) == 5


def test_header_prepended_to_each_chunk() -> None:
    """Every chunk carries the header row so the column names provide context."""
    chunks = _chunk_table_csv(_CSV_DOC)
    header = "Tên dịch vụ,Mô tả,Giá (VND)"
    for c in chunks:
        assert c.startswith(header), f"chunk missing header: {c!r}"


def test_service_and_price_in_same_chunk() -> None:
    """Atomic semantic guarantee — service name and price never split apart."""
    chunks = _chunk_table_csv(_CSV_DOC)
    # Pick a known row and assert both name + price live in the same chunk.
    matches = [c for c in chunks if "Trẻ hóa da bằng HIFU" in c]
    assert len(matches) == 1
    assert "8999000" in matches[0]


def test_oversized_row_kept_whole() -> None:
    """Row larger than max_chunk_chars is returned whole — atomic semantic unit."""
    huge_row = ",".join(["x" * 200] * 20)  # > DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS
    doc = f"col1,col2\n{huge_row}"
    chunks = _chunk_table_csv(doc)
    assert len(chunks) == 1
    assert huge_row in chunks[0]
    # Sanity: the chunk really is oversized — proves we did not silently truncate.
    assert len(chunks[0]) > DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS


def test_single_row_input_returned_unchanged() -> None:
    """Degenerate input (only header, no data rows) returns the text as-is."""
    chunks = _chunk_table_csv("only,one,line")
    assert chunks == ["only,one,line"]


def test_select_strategy_picks_table_csv_for_csv() -> None:
    """``select_strategy`` returns ``table_csv`` for a pure CSV doc — strict assertion."""
    profile = analyze_document(_CSV_DOC)
    strategy, confidence = select_strategy(profile)
    assert strategy == "table_csv", f"expected table_csv, got {strategy!r}"
    assert confidence > 0.9


def test_smart_chunk_dispatches_table_csv() -> None:
    """End-to-end: ``smart_chunk`` honours the strategy and returns row-as-chunk output."""
    chunks = smart_chunk(_CSV_DOC)
    assert len(chunks) == 5
    # Every chunk is "header\nrow" (single newline separator).
    for c in chunks:
        assert c.count("\n") == 1


def test_prose_doc_does_not_pick_table_csv() -> None:
    """Guard: comma-rich prose with sentence punctuation MUST NOT pick table_csv."""
    prose = (
        "I love apples, oranges, and bananas. "
        "However, pears, grapes, and peaches are better.\n"
        "Today, the weather is nice, sunny, and warm."
    )
    assert _is_csv_format(prose) is False
    profile = analyze_document(prose)
    strategy, _ = select_strategy(profile)
    assert strategy != "table_csv"
