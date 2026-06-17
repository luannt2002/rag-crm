"""Unit tests for shared/dedup.py — Jaccard similarity + duplicate-pair finder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ragbot.shared.dedup import find_duplicate_pairs, jaccard


def test_identical_chunks_jaccard_one() -> None:
    """Same text → similarity 1.0."""
    text = "Dịch vụ chăm sóc da công nghệ cao tại trung tâm"
    assert jaccard(text, text) == 1.0


def test_disjoint_chunks_jaccard_zero() -> None:
    """No shared tokens → similarity 0.0."""
    a = "alpha beta gamma"
    b = "kappa lambda mu"
    assert jaccard(a, b) == 0.0


def test_partial_overlap_jaccard() -> None:
    """Two of four unique tokens overlap → 2 / 6 ≈ 0.333."""
    a = "alpha beta gamma delta"
    b = "alpha beta epsilon zeta"
    sim = jaccard(a, b)
    assert 0.30 < sim < 0.40


def test_find_duplicate_pairs_above_threshold() -> None:
    """Three chunks where chunks[0] and chunks[1] are near-identical → 1 pair."""
    base = "Liệu trình chăm sóc da bao gồm bước làm sạch toner và dưỡng ẩm hàng ngày tại spa của trung tâm"
    near_dup = base + " mới cập nhật"  # identical-ish; ratio well above 0.85
    distinct = (
        "Triệt lông vĩnh viễn bằng laser diode công nghệ Ý áp dụng cho mọi vùng da nhạy cảm"
    )
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    chunks = [
        {"id": "id-older", "content": base, "created_at": now},
        {"id": "id-newer", "content": near_dup, "created_at": now + timedelta(hours=1)},
        {"id": "id-other", "content": distinct, "created_at": now + timedelta(hours=2)},
    ]
    pairs = find_duplicate_pairs(chunks, threshold=0.85, min_chars=50)
    assert len(pairs) == 1
    kept, dropped = pairs[0]
    assert kept == "id-older"
    assert dropped == "id-newer"


def test_find_duplicate_pairs_skips_short_chunks() -> None:
    """Chunks under min_chars are excluded even when 100% identical."""
    short = "abc xyz"
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    chunks = [
        {"id": "a", "content": short, "created_at": now},
        {"id": "b", "content": short, "created_at": now + timedelta(seconds=1)},
    ]
    pairs = find_duplicate_pairs(chunks, threshold=0.85, min_chars=50)
    assert pairs == []


def test_keeps_older_chunk_in_pair() -> None:
    """When the newer chunk is listed first, older still wins."""
    long_text = (
        "Bảng giá dịch vụ chăm sóc da công nghệ cao bao gồm liệu trình "
        "treatment cho mặt và toàn thân với chuyên viên có chứng chỉ"
    )
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    chunks = [
        {"id": "newer", "content": long_text, "created_at": now + timedelta(days=1)},
        {"id": "older", "content": long_text, "created_at": now},
    ]
    pairs = find_duplicate_pairs(chunks, threshold=0.85, min_chars=50)
    assert len(pairs) == 1
    kept, dropped = pairs[0]
    assert kept == "older"
    assert dropped == "newer"
