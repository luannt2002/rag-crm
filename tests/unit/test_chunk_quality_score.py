"""Unit tests — CleanBase chunk quality scoring (observability heuristic).

Covers ``score_chunk_quality`` + ``emit_chunk_quality_event`` in
``ragbot.application.services.contextual_chunk_enrichment``.

Pure-function tests (no LLM, no DB) — heuristic is deterministic so each
component contribution is asserted directly. Sub-threshold case is verified
via ``structlog.testing.capture_logs`` to ensure the event name + payload
schema stay stable for downstream alerting.
"""
from __future__ import annotations

from structlog.testing import capture_logs

from ragbot.application.services.contextual_chunk_enrichment import (
    emit_chunk_quality_event,
    score_chunk_quality,
)
from ragbot.shared.constants import (
    DEFAULT_CLEANBASE_MAX_WORDS,
    DEFAULT_CLEANBASE_MIN_WORDS,
    DEFAULT_CLEANBASE_QUALITY_THRESHOLD,
)


def test_complete_vn_sentence_scores_high() -> None:
    """Complete VN sentence ≥20 words ending '.' hits 3 components: completeness,
    word-band, last-token-shape. Numeric component absent (neutral) → 0.75."""
    chunk = (
        "Hệ thống ragbot cung cấp khả năng truy xuất ngữ nghĩa thông minh "
        "cho doanh nghiệp với nhiều tính năng tùy biến và bảo mật cao "
        "phù hợp cho mọi quy mô triển khai khác nhau hiện nay."
    )
    score = score_chunk_quality(chunk)
    assert score >= 0.75
    # Three deterministic components fire (sentence end + word band + clean
    # last token); numeric branch is neutral because no keyword present.
    assert score == 0.75


def test_truncated_word_at_end_scores_below_half() -> None:
    """Truncated last token (length 2, mid-word cut) loses last-token-shape
    AND completeness components → score capped <0.5."""
    chunk = (
        "Đây là một đoạn văn bản dài có đủ số lượng từ vượt quá ngưỡng tối "
        "thiểu nhưng kết thúc bằng một từ bị cắt giữa ch"
    )
    score = score_chunk_quality(chunk)
    assert score < 0.5


def test_empty_string_returns_zero() -> None:
    """Empty input has no signal to grade → exact 0.0 floor."""
    assert score_chunk_quality("") == 0.0
    assert score_chunk_quality("   \n\t  ") == 0.0


def test_word_count_above_max_loses_band_component() -> None:
    """Long paragraph >MAX_WORDS forfeits the word-band component (0.25).
    Other components still fire so the loss is exactly the band weight."""
    long_chunk = (
        " ".join(["alpha"] * (DEFAULT_CLEANBASE_MAX_WORDS + 50))
        + " omega."
    )
    score = score_chunk_quality(long_chunk)
    # Completeness (.) + last-token-shape ("omega") fire → 0.5. Band absent.
    assert score == 0.5


def test_word_count_below_min_loses_band_component() -> None:
    """Sparse fragment <MIN_WORDS forfeits the word-band component too —
    confirms the band is symmetric (under-shot penalised same as over-shot)."""
    short_chunk = " ".join(["alpha"] * (DEFAULT_CLEANBASE_MIN_WORDS - 5)) + "."
    score = score_chunk_quality(short_chunk)
    # Completeness (.) + last-token-shape ("alpha") fire; word-band absent.
    assert score == 0.5


def test_numeric_keyword_with_digit_activates_numeric_component() -> None:
    """VN keyword 'giá' present + digit present → numeric component fires.
    All 4 components active → score == 1.0."""
    chunk = (
        "Sản phẩm có giá ưu đãi 1500000 đồng cho khách hàng đăng ký mới "
        "trong tháng này và áp dụng tại các chi nhánh trên toàn quốc."
    )
    score = score_chunk_quality(chunk)
    assert score == 1.0


def test_numeric_keyword_without_digit_loses_numeric_component() -> None:
    """Keyword present but NO digit → numeric component does NOT fire,
    confirming the gate enforces 'expected number is actually written'."""
    chunk = (
        "Sản phẩm này có giá ưu đãi cho khách hàng đăng ký mới trong tháng "
        "này và áp dụng tại các chi nhánh trên toàn quốc theo chính sách."
    )
    score = score_chunk_quality(chunk)
    # Completeness + word-band + last-token-shape fire; numeric absent.
    assert score == 0.75


def test_below_threshold_emits_structured_event() -> None:
    """Sub-threshold chunk emits ``chunk_quality_below_threshold`` warn event
    with score, threshold, chunk_index, document_title fields populated."""
    bad_chunk = "ng"  # truncated, single token, <MIN_WORDS, no terminal punct.
    score = score_chunk_quality(bad_chunk)
    assert score < DEFAULT_CLEANBASE_QUALITY_THRESHOLD

    with capture_logs() as logs:
        emit_chunk_quality_event(
            score=score,
            threshold=DEFAULT_CLEANBASE_QUALITY_THRESHOLD,
            chunk_index=7,
            document_title="policy_handbook",
        )

    matches = [e for e in logs if e["event"] == "chunk_quality_below_threshold"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["log_level"] == "warning"
    assert entry["score"] == score
    assert entry["threshold"] == DEFAULT_CLEANBASE_QUALITY_THRESHOLD
    assert entry["chunk_index"] == 7
    assert entry["document_title"] == "policy_handbook"
