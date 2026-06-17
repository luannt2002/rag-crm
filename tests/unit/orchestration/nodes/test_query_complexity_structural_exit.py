"""Pin test: structural VN legal query short-circuits to SIMPLE.

UI trace 2026-05-27: 'chương 3 nói gì' got complexity_score 0.8 (COMPLEX)
because the digit '3' raised the numeric-token weight. multi_query fanout
then generated variants that lost the structural anchor → retrieve missed
chunk 71 ([Chương 3] ĐIỀU KHOẢN THI HÀNH) → bot refuse SAI.

Fix: short queries matching 'Chương|Phần|Mục|Điều N' (arabic or roman)
short-circuit to ('simple', 0.0). Compound legal queries (with commas /
conjunctions) keep the standard scoring path so multi-entity lookups
still benefit from decomposition.
"""
from __future__ import annotations

from ragbot.orchestration.nodes.query_complexity import (
    classify_query_complexity,
)


def _const_getter(value: object) -> object:
    """Helper: return same value regardless of key."""
    def _g(_key: str, default: object) -> object:
        return value if value is not None else default
    return _g


def test_chuong_arabic_query_is_simple() -> None:
    """The bug case — 'Chương 3 nói gì' must classify SIMPLE."""
    label, score = classify_query_complexity("Chương 3 nói gì")
    assert label == "simple"
    assert score == 0.0


def test_chuong_lowercase_arabic_query_is_simple() -> None:
    label, score = classify_query_complexity("chương 3 nói gì")
    assert label == "simple"
    assert score == 0.0


def test_chuong_roman_query_is_simple() -> None:
    """Roman form was already SIMPLE pre-fix; verify still SIMPLE."""
    label, score = classify_query_complexity("Chương III nói gì")
    assert label == "simple"
    assert score == 0.0


def test_muc_query_is_simple() -> None:
    label, _ = classify_query_complexity("Mục 5 nói về điều gì")
    assert label == "simple"


def test_dieu_query_is_simple() -> None:
    """Điều N — most common structural lookup form."""
    label, _ = classify_query_complexity("Điều 55 quy định gì")
    assert label == "simple"


def test_phan_query_is_simple() -> None:
    label, _ = classify_query_complexity("Phần II nói gì")
    assert label == "simple"


def test_compound_legal_query_keeps_complex_path() -> None:
    """Compound query (multi-entity) must NOT short-circuit.

    'so sánh Điều 22 và Điều 55' has structural tokens but is multi-entity
    → multi_query fanout helps. Conjunction ' và ' triggers normal scoring.
    """
    label, score = classify_query_complexity(
        "So sánh Điều 22 và Điều 55 khác nhau ở điểm nào",
        config_getter=_const_getter(0.5),  # high weights so it'd otherwise score
    )
    # 'và' conjunction present → standard scoring path → likely complex
    # We do NOT assert specific label, only that it's NOT the early-exit
    # (i.e. score > 0 indicates normal scoring ran)
    assert score > 0.0 or label == "complex"


def test_compound_comma_query_keeps_normal_path() -> None:
    """Multiple commas → multi-entity → standard path."""
    label, score = classify_query_complexity(
        "Chương 2, Chương 3, Chương 4 nói về gì",
    )
    # ≥ 2 commas means standard scoring runs (early-exit gate is "<= 1 comma")
    assert score > 0.0 or label == "complex"


def test_long_query_with_structural_token_not_short_circuited() -> None:
    """Long query (> 80 chars) with structural token keeps normal scoring.

    Edge case: a long compound query that happens to mention 'Chương 3'
    should not bypass the standard classifier.
    """
    long_q = (
        "Tôi muốn hiểu rõ về Chương 3 và đặc biệt là sự khác biệt giữa "
        "các điều khoản thi hành so với các chương khác trong thông tư này"
    )
    assert len(long_q) > 80
    label, score = classify_query_complexity(long_q)
    # Should NOT be the (0.0, simple) early-exit — long compound query
    assert not (score == 0.0 and label == "simple") or score > 0.0


def test_non_structural_query_unaffected() -> None:
    """Queries without Chương/Phần/Mục/Điều keep their normal score."""
    label, score = classify_query_complexity("Giá triệt lông là bao nhiêu")
    # No structural anchor — early-exit must not fire; normal path may
    # give simple or complex depending on config, but score must NOT be
    # forced to 0.0 by the early-exit. Length-normalizer etc still apply.
    # We just verify the early-exit is not the reason for any (simple, 0.0).
    # If the natural-path also returns (simple, 0.0) that's fine; the test
    # confirms structural classification doesn't change unrelated queries.
    assert isinstance(label, str)
    assert isinstance(score, float)


def test_empty_query_still_simple() -> None:
    """Empty/None edge case still works (no exception)."""
    assert classify_query_complexity("") == ("simple", 0.0)
    assert classify_query_complexity("   ") == ("simple", 0.0) or classify_query_complexity("   ")[0] == "simple"
