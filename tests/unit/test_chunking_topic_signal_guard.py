"""Multi-topic guard for whole-doc chunking — `_count_topic_signals` heuristic.

Anti-pattern: a 4655-char doc with 7 disjoint topics (price list / packages /
massage / hair-wash / hair-removal / warranty / featured services) was stored
as a single 1024-dim embedding. Centroid-of-7-topics → cosine similarity for
any single-topic query collapsed to 0.05–0.27. The guard rejects multi-topic
docs from the whole-doc fast path so each topic embeds into its own chunk.
"""
from __future__ import annotations

from ragbot.shared.chunking import _count_topic_signals
from ragbot.shared.constants import (
    DEFAULT_TOPIC_PARAGRAPH_MIN_CHARS,
    DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS,
    WHOLE_DOC_THRESHOLD_CHARS,
)


def test_threshold_constant_lowered_to_1500() -> None:
    """Whole-doc threshold lowered from 8000 to 1500 — large prose docs no
    longer fast-path into a single chunk."""
    assert WHOLE_DOC_THRESHOLD_CHARS == 1500


def test_max_topic_signals_default_two() -> None:
    assert DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS == 2


def test_empty_or_blank_doc_zero_signals() -> None:
    assert _count_topic_signals("") == 0
    assert _count_topic_signals("   \n  \n") == 0


def test_single_topic_short_prose_below_guard() -> None:
    """800-char single-paragraph prose → topic_signals < 2, fast path applies."""
    text = (
        "Spa của chúng tôi cung cấp dịch vụ chăm sóc da hiện đại. "
        * 12
    )
    assert len(text) >= 600
    assert _count_topic_signals(text) <= DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS


def test_two_paragraph_blocks_one_signal() -> None:
    """Two paragraphs ≥ MIN_CHARS apart → exactly 1 signal (n - 1)."""
    para = "x" * (DEFAULT_TOPIC_PARAGRAPH_MIN_CHARS + 10)
    text = para + "\n\n" + para
    signals = _count_topic_signals(text)
    assert signals == 1


def test_markdown_three_h2_rejects_whole_doc() -> None:
    """3 H2 headers → signals ≥ 3 → whole-doc rejected (over default 2)."""
    text = (
        "## Section A\nFoo bar baz.\n\n"
        "## Section B\nLorem ipsum.\n\n"
        "## Section C\nDolor sit amet.\n"
    )
    signals = _count_topic_signals(text)
    assert signals >= 3
    assert signals > DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS


def test_numbered_markers_count_in_threes() -> None:
    """3+ numbered list markers contribute n // 3 signals."""
    text = (
        "1. Foo line one is here.\n"
        "2. Bar line two is here.\n"
        "3. Baz line three is here.\n"
    )
    signals = _count_topic_signals(text)
    assert signals >= 1


def test_uppercase_section_headers_count() -> None:
    """UPPERCASE-style short headers act as section markers."""
    text = (
        "GIỚI THIỆU\nNội dung phần một ngắn gọn.\n\n"
        "DỊCH VỤ CHÍNH\nNội dung phần hai ngắn gọn.\n\n"
        "LIÊN HỆ\nThông tin liên hệ ngắn gọn.\n"
    )
    signals = _count_topic_signals(text)
    assert signals >= 3


def test_multi_topic_csv_with_section_headers_rejects_whole_doc() -> None:
    """Multi-section CSV + headers → ≥ 4 topic signals (whole-doc rejected)."""
    text = (
        "BẢNG GIÁ DỊCH VỤ\n"
        "Tên,Giá\nA,100\nB,200\nC,300\n\n"
        "GÓI BUFFET\n"
        "Tên,Giá\nGói 6,6000000\nGói 7,7000000\nGói 10,10000000\n\n"
        "DỊCH VỤ NỔI BẬT\n"
        "1. Nâng cơ trẻ hóa với máy hiện đại đầy đủ tính năng.\n"
        "2. Ultherapy chuẩn FDA cho da căng mịn.\n"
        "3. Tráng gương kim cương quy trình hơn 20 bước chuyên sâu.\n\n"
        "DỊCH VỤ MASSAGE\n"
        "Tên,Giá\nMassage cổ vai gáy,300000\nMassage toàn thân,500000\n\n"
        "DỊCH VỤ GỘI ĐẦU\n"
        "Tên,Giá\nGội thảo dược,150000\nGội dưỡng sinh,200000\n"
    )
    signals = _count_topic_signals(text)
    assert signals >= 4, f"expected ≥4 multi-topic signals, got {signals}"
    assert signals > DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS
