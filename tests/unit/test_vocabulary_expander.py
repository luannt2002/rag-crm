"""Unit tests for VocabularyExpander — generic vocab base.

COMPLEXITY_SCORE: 2 — single module, clear pattern, zero business logic.
ADVISOR_NEEDED: no

Tests cover:
- expand_query: original first, abbreviation expansion, English→VN, no-match
- detect_abbreviations: short tokens only
- custom vocab override
- enrich_state: state injection + no-op
- max caps (matches, expansions)
- domain-neutral check: no brand/industry terms in GENERIC_VOCABULARY
"""
from __future__ import annotations

import pytest

from ragbot.application.services.vocabulary_expander import (
    GENERIC_VOCABULARY,
    VocabularyExpander,
    VocabularyMatch,
    get_default_expander,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def expander() -> VocabularyExpander:
    return VocabularyExpander()


@pytest.fixture
def expander_disabled() -> VocabularyExpander:
    return VocabularyExpander(enabled=False)


# ---------------------------------------------------------------------------
# Test 1 — expand_query returns original query first
# ---------------------------------------------------------------------------

def test_expand_query_returns_original_first(expander: VocabularyExpander) -> None:
    variants = expander.expand_query("ko biết giá thế nào")
    assert len(variants) >= 1
    assert variants[0] == "ko biết giá thế nào"


# ---------------------------------------------------------------------------
# Test 2 — expand abbreviation "ko" → "không"
# ---------------------------------------------------------------------------

def test_expand_abbreviation_ko_to_khong(expander: VocabularyExpander) -> None:
    variants = expander.expand_query("ko có gì")
    flat = " ".join(variants)
    assert "không" in flat, f"Expected 'không' in variants: {variants}"


# ---------------------------------------------------------------------------
# Test 3 — expand English "price" → "giá"
# ---------------------------------------------------------------------------

def test_expand_english_to_vietnamese(expander: VocabularyExpander) -> None:
    variants = expander.expand_query("price bao nhiêu")
    flat = " ".join(variants)
    assert "giá" in flat or "chi phí" in flat, (
        f"Expected Vietnamese expansion in: {variants}"
    )


# ---------------------------------------------------------------------------
# Test 4 — no match returns only original
# ---------------------------------------------------------------------------

def test_expand_no_match_returns_only_original(expander: VocabularyExpander) -> None:
    # Query with no abbreviations and no generic vocab tokens
    query = "xin chào"
    variants = expander.expand_query(query)
    assert variants == [query]


# ---------------------------------------------------------------------------
# Test 5 — detect_abbreviations returns only short tokens (≤4 chars)
# ---------------------------------------------------------------------------

def test_detect_abbreviations(expander: VocabularyExpander) -> None:
    abbrevs = expander.detect_abbreviations("tks ko dc")
    # "tks" (3 chars), "ko" (2 chars) should be detected
    assert "ko" in abbrevs
    assert abbrevs["ko"] == "không"


# ---------------------------------------------------------------------------
# Test 6 — custom vocab overrides generic
# ---------------------------------------------------------------------------

def test_custom_vocab_overrides_generic(expander: VocabularyExpander) -> None:
    custom = {"ko": ["nope"]}  # override generic "ko" → ["không"]
    matches = expander.detect_matches("ko rõ", bot_custom_vocab=custom)
    ko_match = next((m for m in matches if m.original_token == "ko"), None)
    assert ko_match is not None
    assert ko_match.expansions == ["nope"]
    assert ko_match.source == "bot_custom"


# ---------------------------------------------------------------------------
# Test 7 — enrich_state adds vocabulary to state["context_base"]
# ---------------------------------------------------------------------------

def test_enrich_state_adds_vocabulary(expander: VocabularyExpander) -> None:
    state: dict = {}
    result = expander.enrich_state(state, "ko biết price")
    assert "context_base" in result
    vocab = result["context_base"]["vocabulary"]
    assert "matches" in vocab
    assert len(vocab["matches"]) >= 1
    assert vocab["method"] == "application_layer_generic_vocab"


# ---------------------------------------------------------------------------
# Test 8 — enrich_state is no-op when no match
# ---------------------------------------------------------------------------

def test_enrich_state_no_op_when_no_match(expander: VocabularyExpander) -> None:
    state: dict = {}
    result = expander.enrich_state(state, "xin chào")
    # context_base should not be set
    assert "context_base" not in result


# ---------------------------------------------------------------------------
# Test 9 — max 5 expansion variants cap (max_expansions + 1 for original)
# ---------------------------------------------------------------------------

def test_max_5_variants_cap() -> None:
    # Create expander with max_expansions=4 → total variants = 5 (original + 4)
    expander = VocabularyExpander(max_expansions=4)
    # Query with many matchable tokens
    query = "ko tks ok promo price discount voucher"
    variants = expander.expand_query(query)
    assert len(variants) <= 5, f"Expected ≤5 variants, got {len(variants)}: {variants}"


# ---------------------------------------------------------------------------
# Test 10 — GENERIC_VOCABULARY is domain-neutral (no brand/industry terms)
# ---------------------------------------------------------------------------

def test_domain_neutral_no_brand_in_dict() -> None:
    """GENERIC_VOCABULARY must not contain industry/brand-specific terms."""
    forbidden_terms = [
        # Spa / beauty
        "facial", "skincare", "massage", "botox", "dermal",
        "liposuction", "whitening", "meso", "collagen",
        # Finance
        "stock", "crypto", "bitcoin", "forex", "trading",
        "portfolio", "etf", "mutual fund",
        # Education / legal
        "tuition", "curriculum", "lawsuit", "attorney", "deposition",
        # Brand names (should be empty, just verify approach)
    ]
    vocab_keys_lower = {k.lower() for k in GENERIC_VOCABULARY}
    violations = [t for t in forbidden_terms if t.lower() in vocab_keys_lower]
    assert not violations, (
        f"Domain-specific terms found in GENERIC_VOCABULARY: {violations}. "
        "Move to bots.custom_vocabulary JSONB per tenant."
    )


# ---------------------------------------------------------------------------
# Test 11 — detect_matches respects max_matches cap
# ---------------------------------------------------------------------------

def test_detect_matches_respects_max_matches_cap() -> None:
    expander = VocabularyExpander(max_matches=3)
    # Query with many matchable tokens
    query = "ko tks ok promo price discount voucher booking cancel"
    matches = expander.detect_matches(query)
    assert len(matches) <= 3


# ---------------------------------------------------------------------------
# Test 12 — enrich_state does not modify state["answer"]
# ---------------------------------------------------------------------------

def test_enrich_state_does_not_touch_answer(expander: VocabularyExpander) -> None:
    state: dict = {"answer": "original answer"}
    result = expander.enrich_state(state, "ko có promo")
    assert result["answer"] == "original answer"


# ---------------------------------------------------------------------------
# Test 13 — disabled expander is no-op
# ---------------------------------------------------------------------------

def test_disabled_expander_is_noop(expander_disabled: VocabularyExpander) -> None:
    state: dict = {}
    result = expander_disabled.enrich_state(state, "ko có promo")
    assert "context_base" not in result
    variants = expander_disabled.expand_query("ko có promo")
    assert variants == ["ko có promo"]


# ---------------------------------------------------------------------------
# Test 14 — VocabularyMatch dataclass integrity
# ---------------------------------------------------------------------------

def test_vocabulary_match_dataclass() -> None:
    m = VocabularyMatch(
        original_token="ko",
        position=0,
        expansions=["không"],
        source="generic",
    )
    assert m.original_token == "ko"
    assert m.position == 0
    assert m.expansions == ["không"]
    assert m.source == "generic"


# ---------------------------------------------------------------------------
# Test 15 — get_default_expander singleton
# ---------------------------------------------------------------------------

def test_get_default_expander_singleton() -> None:
    e1 = get_default_expander()
    e2 = get_default_expander()
    assert e1 is e2  # same instance


# ---------------------------------------------------------------------------
# Test 16 — custom vocab adds new keys not in generic
# ---------------------------------------------------------------------------

def test_custom_vocab_adds_new_keys(expander: VocabularyExpander) -> None:
    custom = {"myterm": ["expansion1", "expansion2"]}
    matches = expander.detect_matches("myterm here", bot_custom_vocab=custom)
    found = [m for m in matches if m.original_token == "myterm"]
    assert len(found) == 1
    assert found[0].source == "bot_custom"
    assert "expansion1" in found[0].expansions
