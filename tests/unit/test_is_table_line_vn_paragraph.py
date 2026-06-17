"""Stream A Phase 4 — `_is_table_line` must NOT mis-detect VN paragraphs.

Bug (G5): the CSV row heuristic counts commas without weighing line length
or token density, so prose like "Giá gói A, B, C khoảng 100k…" gets flagged
as a CSV row and breaks the document into bogus tabular chunks.

These tests pin the desired behaviour after Phase 4 lands. They are RED
today — `_is_table_line` returns True for several of these paragraphs.
"""
from __future__ import annotations

import pytest

from ragbot.shared.chunking import _is_table_line


# ── True positives we MUST keep detecting ──────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "Mép, 899000",
        "Tên,Giá,Vùng,Combo",
        "Triệt lông Diode Laser,Mép,899.000đ,129.000/buổi,Mua 10 tặng 5",
        "| Vùng | Combo | Giá lẻ |",
        "|---|---|---|",
    ],
)
def test_real_table_lines_still_detected(line: str) -> None:
    assert _is_table_line(line), f"regression: real table row no longer detected: {line!r}"


# ── False positives that Phase 4 must KILL ─────────────────────────────


@pytest.mark.parametrize(
    "paragraph",
    [
        "Giá gói A, B, C tại Dr. Medispa khoảng 100k đến 500k tùy dịch vụ.",
        "Spa cung cấp các dịch vụ chăm sóc da, triệt lông, gội đầu, massage.",
        "Combo gồm 10 buổi, áp dụng cho vùng mép, mặt, nách hoặc bikini.",
        "Khách có thể thanh toán bằng tiền mặt, chuyển khoản, hoặc thẻ tín dụng.",
        "Liệu trình kéo dài 4, 6, hoặc 10 buổi tùy nhu cầu của khách hàng.",
    ],
)
def test_vn_paragraph_with_commas_not_misdetected(paragraph: str) -> None:
    """Long VN sentence with ≥2 commas must NOT be flagged as a CSV row."""
    assert not _is_table_line(paragraph), (
        f"false positive: VN paragraph wrongly flagged as CSV row: {paragraph!r}"
    )
