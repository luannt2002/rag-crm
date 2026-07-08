"""Deterministic non-numeric grounding — scope-over-extension detector.

Root cause (deep-analysis 2026-07-08, DB-verified): warranty corpus scopes to
"lốp xe du lịch (PCR)" (`xe tải`=0 hits) but the bot affirms "bao gồm cả lốp xe
tải". numeric_fidelity is number-only; brand_scope is denial-only → this false
AFFIRMATIVE claim is un-gated. detect_scope_overextension flags the affirmed
object token ("tải") that is absent from the served context. Phrases are
config-injected (no vocab in src); tokens compared by shape + diacritic-normalized.
"""
from __future__ import annotations

from ragbot.shared.claim_fidelity import detect_scope_overextension

# Precise scope-affirmation phrases (config data in production; the test supplies
# its own so the detector carries no Vietnamese literal).
_PHRASES = ("bao gồm cả", "bao gồm thêm")
_WARRANTY_CTX = [
    "[I. Phạm vi áp dụng] Áp dụng cho tất cả các sản phẩm lốp xe du lịch (PCR) "
    "Landspider và Rovelo có số seri hợp lệ.",
]


def test_flags_object_absent_from_context() -> None:
    """The real bug: 'bao gồm cả lốp xe tải' while context is PCR/du lịch only."""
    out = detect_scope_overextension(
        "Chính sách bảo hành áp dụng cho tất cả, bao gồm cả lốp xe tải ạ.",
        _WARRANTY_CTX,
        _PHRASES,
    )
    assert "tai" in out  # "tải" normalized; absent from served context


def test_grounded_object_not_flagged() -> None:
    """Affirming something the context DOES contain must not fire (FP guard)."""
    out = detect_scope_overextension(
        "Bảo hành bao gồm cả lốp xe du lịch ạ.",
        _WARRANTY_CTX,
        _PHRASES,
    )
    assert out == []  # lốp/du/lịch all appear in the served warranty chunk


def test_no_affirmation_phrase_no_flag() -> None:
    """A grounded descriptive claim with NO scope-affirmation phrase is ignored.

    ('Hàn Quốc' is a GROUNDED spa claim — this gate must never touch a claim that
    is not introduced by a configured scope-affirmation phrase.)"""
    out = detect_scope_overextension(
        "Dr. Medispa dùng công nghệ Diode Laser lạnh của Hàn Quốc.",
        ["... Diode Laser lạnh hiện đại của Hàn Quốc ..."],
        _PHRASES,
    )
    assert out == []


def test_empty_phrases_is_noop() -> None:
    assert detect_scope_overextension("bao gồm cả lốp xe tải", _WARRANTY_CTX, ()) == []


def test_no_served_context_stays_silent() -> None:
    assert detect_scope_overextension("bao gồm cả lốp xe tải", [], _PHRASES) == []


def test_diacritic_and_case_insensitive_membership() -> None:
    """'Xe Tải' affirmed while served has 'xe tai' → grounded, no flag."""
    out = detect_scope_overextension("BAO GỒM CẢ Xe Tải", ["dịch vụ xe tai"], _PHRASES)
    assert out == []


def test_shared_category_token_not_flagged() -> None:
    """'lốp' appears in served → only the discriminating absent token flags."""
    out = detect_scope_overextension(
        "bao gồm cả lốp xe tải", _WARRANTY_CTX, _PHRASES,
    )
    assert "lop" not in out  # 'lốp' is in the served chunk → grounded
