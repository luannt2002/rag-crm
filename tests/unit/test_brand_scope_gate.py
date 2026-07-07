"""002-B1: brand-scope denial detector — a bot must not claim it does NOT carry
a brand the corpus actually stocks.

Root cause (truth-audit step20, DB-verified): B-011/G-077/G-078 — the bot
answered "Dạ bên em chưa phân phối hãng Rovelo ạ" while document_service_index
holds 50+ Rovelo SKUs. numeric-fidelity is blind (no number). B-079 Michelin is
the correct-refusal control (0 Michelin rows) — must NOT flag.

The detector is PURE + domain/language-neutral: the negation phrases are
injected from config (never hardcoded), and the brand token is extracted by
proper-noun shape, not a brand vocabulary. Whether the brand is actually stocked
is decided by the caller (a DSI existence query), not here.
"""
from __future__ import annotations

from ragbot.shared.brand_scope import detect_denied_brand

# Language-neutral: phrases come from config in production; the test supplies its
# own so the detector carries no Vietnamese literal.
_PHRASES = ("chưa phân phối hãng", "không phân phối hãng", "chưa có hãng")


def test_detects_brand_after_negation() -> None:
    """B-011/G-077/G-078: 'chưa phân phối hãng Rovelo' → returns 'Rovelo'."""
    b = detect_denied_brand("Dạ bên em chưa phân phối hãng Rovelo ạ.", negation_phrases=_PHRASES)
    assert b is not None
    assert b.lower() == "rovelo"


def test_prefers_brand_also_in_question() -> None:
    b = detect_denied_brand(
        "Dạ chưa phân phối hãng Rovelo ạ, anh xem hãng khác nhé.",
        negation_phrases=_PHRASES,
        question="Cho anh giá lốp 205/60R16 của Rovelo.",
    )
    assert b.lower() == "rovelo"


def test_no_negation_returns_none() -> None:
    """A normal answer with brands but no denial → nothing to check."""
    assert detect_denied_brand(
        "Dạ lốp Rovelo 195/65R15 giá 981.000đ ạ.", negation_phrases=_PHRASES
    ) is None


def test_michelin_denial_still_extracted() -> None:
    """The detector EXTRACTS 'Michelin' too — the DSI-existence check (caller)
    is what distinguishes a true refusal (0 rows) from a false one. B-079's
    non-regression is enforced at the gate layer, not here."""
    b = detect_denied_brand("Dạ bên em chưa phân phối hãng Michelin ạ.", negation_phrases=_PHRASES)
    assert b is not None
    assert b.lower() == "michelin"


def test_size_token_not_mistaken_for_brand() -> None:
    """A bare size/number after the phrase must not be returned as a brand."""
    b = detect_denied_brand("Dạ chưa phân phối hãng 205 nào ạ.", negation_phrases=_PHRASES)
    assert b is None or not b.isdigit()


def test_empty_phrases_returns_none() -> None:
    """No config phrases (locale unseeded) → gate is silent (fail-open)."""
    assert detect_denied_brand("chưa phân phối hãng Rovelo", negation_phrases=()) is None
