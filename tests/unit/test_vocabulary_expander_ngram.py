"""Unit tests for VocabularyExpander n-gram phase + VN negation guard (T2.S9).

COMPLEXITY_SCORE: 2 — single module, deterministic windows, no I/O.
ADVISOR_NEEDED: no

Goals (Win-MVP T1-Smartness):

- Bigram / trigram phrases land as ``VocabularyMatch`` only when the surface
  phrase is in the merged vocab (generic + per-bot custom). Single tokens
  remain expanded — n-gram is additive.
- ``"trông trẻ"`` (childcare query) does NOT pick up an unrelated compound
  ``"trẻ hóa da"`` — the V2.5 HALLU_MISINTERPRET regression we are paying off.
- Negation guard suppresses ``"bán retail"`` when preceded by ``"không"``,
  ``"chưa"``, etc. — n-gram must not flip phrase intent.
- Performance: 100-token input completes well under 50 ms — quadratic-ish
  blowup from the n-gram windows must stay bounded by ``DEFAULT_VOCAB_NGRAM_MAX_N``.
"""
from __future__ import annotations

import time

import pytest

from ragbot.application.services.vocabulary_expander import (
    VocabularyExpander,
    VocabularyMatch,
)
from ragbot.shared.constants import (
    DEFAULT_VN_NEGATION_TOKENS,
    DEFAULT_VOCAB_NGRAM_MAX_N,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_vocab() -> dict[str, list[str]]:
    """A minimal merged dict that exercises the n-gram phase.

    Keys deliberately mix unigrams (``"trẻ"``) with multi-word phrases
    (``"trẻ hóa"``, ``"bán retail"``) so we can assert each window is
    matched on the right surface form, not the loose union of tokens.
    """
    return {
        "trẻ": ["young"],
        "trẻ hóa": ["anti-aging", "skin rejuvenation"],
        "trẻ hóa da": ["facial rejuvenation"],
        "bán retail": ["retail sales"],
        "trông": ["look", "appear"],
    }


@pytest.fixture
def expander(base_vocab: dict[str, list[str]]) -> VocabularyExpander:
    # Pass an explicit base_vocab so the test is deterministic and does not
    # depend on the platform-default GENERIC_VOCABULARY shipping these keys.
    return VocabularyExpander(base_vocab=base_vocab, max_matches=20)


# ---------------------------------------------------------------------------
# Test 1 — bigram in vocab matches; surface form preserved
# ---------------------------------------------------------------------------

def test_bigram_in_vocab_matches(expander: VocabularyExpander) -> None:
    matches = expander.detect_matches("trẻ hóa da hiệu quả")
    bigram = next(
        (m for m in matches if m.original_token == "trẻ hóa" and m.n == 2),
        None,
    )
    assert bigram is not None, f"Expected bigram match 'trẻ hóa', got: {matches}"
    assert "anti-aging" in bigram.expansions


# ---------------------------------------------------------------------------
# Test 2 — trigram in vocab matches
# ---------------------------------------------------------------------------

def test_trigram_in_vocab_matches(expander: VocabularyExpander) -> None:
    matches = expander.detect_matches("dịch vụ trẻ hóa da tốt nhất")
    trigram = next(
        (m for m in matches if m.original_token == "trẻ hóa da" and m.n == 3),
        None,
    )
    assert trigram is not None, f"Expected trigram 'trẻ hóa da' in: {matches}"
    assert "facial rejuvenation" in trigram.expansions


# ---------------------------------------------------------------------------
# Test 3 — bigram NOT in vocab is silently skipped
# ---------------------------------------------------------------------------

def test_bigram_not_in_vocab_is_skipped(expander: VocabularyExpander) -> None:
    """``"trẻ con"`` is NOT in the test vocab → no bigram emitted."""
    matches = expander.detect_matches("nhìn trẻ con vui")
    bigrams = [m for m in matches if m.n == 2]
    assert all(m.original_token != "trẻ con" for m in bigrams), (
        f"'trẻ con' should not match (not in vocab): {matches}"
    )


# ---------------------------------------------------------------------------
# Test 4 — V2.5 regression: "trông trẻ" must NOT match "trẻ hóa da"
# ---------------------------------------------------------------------------

def test_trong_tre_does_not_match_tre_hoa_da(expander: VocabularyExpander) -> None:
    """The original V2.5 misinterpret: query "trông trẻ" (childcare) keyword-
    matched corpus "trẻ hóa da" (anti-aging). N-gram phase must operate on
    contiguous windows of THE QUERY, never on the corpus side, so a query
    bigram "trông trẻ" (not in vocab) yields no compound match.
    """
    matches = expander.detect_matches("dịch vụ trông trẻ ban ngày")

    # No bigram surface form "trẻ hóa" or "trẻ hóa da" can come out of this
    # query — those tokens never appear contiguously.
    forbidden = {"trẻ hóa", "trẻ hóa da"}
    for m in matches:
        assert m.original_token not in forbidden, (
            f"BUG — query 'trông trẻ' should not produce '{m.original_token}'"
        )

    # And "trông trẻ" is not in vocab so it is also silently skipped.
    assert all(
        m.original_token != "trông trẻ" for m in matches if m.n == 2
    ), f"Bigram 'trông trẻ' is not vocab-defined: {matches}"


# ---------------------------------------------------------------------------
# Test 5 — negation guard suppresses positive bigram match
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "negation_word",
    sorted(DEFAULT_VN_NEGATION_TOKENS),
)
def test_negation_guard_suppresses_bigram(
    expander: VocabularyExpander, negation_word: str
) -> None:
    """``"không bán retail"`` / ``"chưa bán retail"`` must NOT positively
    match the bigram ``"bán retail"`` — the user is explicitly negating it.
    """
    query = f"shop này {negation_word} bán retail"
    matches = expander.detect_matches(query)
    bigrams = [m for m in matches if m.n == 2]
    assert all(m.original_token != "bán retail" for m in bigrams), (
        f"Negation '{negation_word}' must suppress 'bán retail': {matches}"
    )


# ---------------------------------------------------------------------------
# Test 6 — negation OUTSIDE lookback window does NOT suppress
# ---------------------------------------------------------------------------

def test_negation_outside_lookback_does_not_suppress(
    expander: VocabularyExpander,
) -> None:
    """Default lookback = 2 tokens. A negation 4 tokens to the left should
    not flip the phrase intent (sentence-level scope kept tight on purpose).
    """
    # tokens: "không" "phải" "shop" "này" "bán" "retail"
    # window for "bán retail" starts at idx 4 → left window = ["này", "shop"] (lookback=2)
    # → no negation in window → bigram should match.
    matches = expander.detect_matches("không phải shop này bán retail")
    bigrams = [m for m in matches if m.original_token == "bán retail"]
    assert bigrams, f"Negation 4-tokens-left must NOT suppress: {matches}"


# ---------------------------------------------------------------------------
# Test 7 — single-token expansion still works (additive, not replacement)
# ---------------------------------------------------------------------------

def test_unigram_expansion_still_active(expander: VocabularyExpander) -> None:
    """Bumping in n-gram support must not regress the unigram phase."""
    matches = expander.detect_matches("một bạn trẻ tài năng")
    unigrams = [m for m in matches if m.n == 1]
    tre = next((m for m in unigrams if m.original_token == "trẻ"), None)
    assert tre is not None, f"Unigram 'trẻ' must still expand: {matches}"
    assert "young" in tre.expansions


# ---------------------------------------------------------------------------
# Test 8 — additive: unigram AND bigram BOTH emitted when both apply
# ---------------------------------------------------------------------------

def test_additive_unigram_and_bigram(expander: VocabularyExpander) -> None:
    matches = expander.detect_matches("trẻ hóa da")
    arities = {m.n for m in matches}
    # Expect at least both unigram (trẻ) and bigram (trẻ hóa) — trigram may
    # also fire ("trẻ hóa da") since it's in the vocab.
    assert 1 in arities, f"Expected unigram match: {matches}"
    assert 2 in arities, f"Expected bigram match: {matches}"


# ---------------------------------------------------------------------------
# Test 9 — empty input returns empty match list
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty(expander: VocabularyExpander) -> None:
    assert expander.detect_matches("") == []
    assert expander.detect_matches("   ") == []


# ---------------------------------------------------------------------------
# Test 10 — performance: 100-token input under 50ms
# ---------------------------------------------------------------------------

def test_performance_100_tokens_under_50ms(expander: VocabularyExpander) -> None:
    """Quadratic blowup guard — ``DEFAULT_VOCAB_NGRAM_MAX_N=3`` keeps the
    inner loop O(N * MAX_N) where N = #tokens. 100 tokens × 3 ≈ 300 windows
    of dict lookups → comfortably below 50 ms on commodity CPU.
    """
    # Build a 100-token query mixing matchable + non-matchable tokens.
    base = "trẻ hóa da và một dịch vụ tốt nhanh chóng "
    # ~10 tokens × 10 = 100 tokens
    query = (base * 10).strip()

    start = time.perf_counter()
    matches = expander.detect_matches(query)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50, f"detect_matches too slow: {elapsed_ms:.1f}ms"
    # Sanity — at least one match landed.
    assert matches, "Expected matches from the synthetic query"


# ---------------------------------------------------------------------------
# Test 11 — n-gram cap respects ``DEFAULT_VOCAB_NGRAM_MAX_N`` (no 4-grams)
# ---------------------------------------------------------------------------

def test_no_quadgram_emitted(base_vocab: dict[str, list[str]]) -> None:
    """Even if the merged vocab were to contain a 4-token phrase, the
    expander cap MUST silently skip it — protects the inner loop from
    quadratic blowup an attacker / mistuned tenant could trigger.
    """
    # Inject a 4-token phrase into the dict so we can confirm it's ignored.
    vocab = dict(base_vocab)
    vocab["trẻ hóa da mặt"] = ["face anti-aging"]  # 4 tokens

    expander = VocabularyExpander(base_vocab=vocab, max_matches=20)
    matches = expander.detect_matches("dịch vụ trẻ hóa da mặt cho khách")
    n_arities = {m.n for m in matches}
    assert all(n <= DEFAULT_VOCAB_NGRAM_MAX_N for n in n_arities), (
        f"4-gram (or higher) leaked through cap: {matches}"
    )


# ---------------------------------------------------------------------------
# Test 12 — per-bot custom dict can supply an n-gram phrase
# ---------------------------------------------------------------------------

def test_bot_custom_ngram_supplied_at_call_time() -> None:
    """Per-bot whitelist arrives via ``bot_custom_vocab`` — no global mutation
    needed. This is how a tenant onboards their own VN compound list.
    """
    expander = VocabularyExpander(base_vocab={}, max_matches=10)
    custom = {"chăm sóc da": ["skin care"]}
    matches = expander.detect_matches(
        "dịch vụ chăm sóc da hiệu quả",
        bot_custom_vocab=custom,
    )
    bigram = next(
        (m for m in matches if m.original_token == "chăm sóc da" and m.n == 3),
        None,
    )
    assert bigram is not None, f"Bot-custom trigram not matched: {matches}"
    assert bigram.source == "bot_custom"
    assert "skin care" in bigram.expansions


# ---------------------------------------------------------------------------
# Test 13 — VocabularyMatch backward compat: ``n`` defaults to 1
# ---------------------------------------------------------------------------

def test_vocabulary_match_default_n_is_1() -> None:
    """Existing callers that construct ``VocabularyMatch`` without ``n``
    must keep working — keeps the data class backward-compatible.
    """
    m = VocabularyMatch(
        original_token="ko",
        position=0,
        expansions=["không"],
        source="generic",
    )
    assert m.n == 1
