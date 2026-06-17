"""Language-gate + DB-driven tests for VocabularyExpander.

Multi-industry / multi-language audit follow-up: built-in
``GENERIC_VOCABULARY`` is Vietnamese-centric (teencode + VN↔EN mixing). A
non-Vietnamese bot MUST NOT silently get ``"k"`` → ``"không"`` expansion on
plain English / Mandarin queries. Per-bot ``custom_vocabulary`` MUST still
apply regardless of language so bot owners can supply their own dict.
"""
from __future__ import annotations

from ragbot.application.services.vocabulary_expander import (
    GENERIC_VOCABULARY,
    VocabularyExpander,
    get_default_expander,
)


# ---------------------------------------------------------------------------
# 1. Vietnamese language gate — built-in dict applies
# ---------------------------------------------------------------------------

class TestViLanguageGate:
    def test_vi_default_loads_generic_vocabulary(self) -> None:
        expander = VocabularyExpander(language="vi")
        # Built-in dict should be the platform GENERIC_VOCABULARY.
        assert expander._base is GENERIC_VOCABULARY  # noqa: SLF001
        assert expander.language == "vi"

    def test_vi_expands_ko_to_khong(self) -> None:
        expander = VocabularyExpander(language="vi")
        variants = expander.expand_query("ko biết gì")
        flat = " ".join(variants)
        assert "không" in flat


# ---------------------------------------------------------------------------
# 2. Non-Vietnamese language gate — built-in dict skipped
# ---------------------------------------------------------------------------

class TestNonViLanguageGate:
    def test_en_skips_built_in_dict(self) -> None:
        expander = VocabularyExpander(language="en")
        # Built-in dict must NOT auto-load for EN.
        assert expander._base == {}  # noqa: SLF001

    def test_en_does_not_expand_bare_ascii_token_k(self) -> None:
        # The classic foot-gun: "k" → "không" would corrupt EN queries.
        expander = VocabularyExpander(language="en")
        variants = expander.expand_query("k events scheduled today")
        # Only original query — no Vietnamese expansion.
        assert variants == ["k events scheduled today"]

    def test_zh_does_not_expand_vietnamese_dict(self) -> None:
        expander = VocabularyExpander(language="zh")
        # Original Mandarin-style query stays as-is.
        variants = expander.expand_query("最贵的套餐 ko")
        # No Vietnamese expansion of "ko" should appear.
        flat = " ".join(variants).lower()
        assert "không" not in flat

    def test_en_enrich_state_no_op_without_custom_vocab(self) -> None:
        expander = VocabularyExpander(language="en")
        state: dict = {}
        result = expander.enrich_state(state, "k events tomorrow")
        # No vocabulary context_base should be set.
        assert "context_base" not in result


# ---------------------------------------------------------------------------
# 3. Bot-custom vocab applies regardless of language
# ---------------------------------------------------------------------------

class TestBotCustomVocabAcrossLanguages:
    def test_en_bot_custom_vocab_still_applies(self) -> None:
        expander = VocabularyExpander(language="en")
        bot_vocab = {"asap": ["as soon as possible"]}
        matches = expander.detect_matches("send asap please", bot_custom_vocab=bot_vocab)
        # Even though built-in dict is empty for EN, custom vocab must work.
        names = {m.original_token for m in matches}
        assert "asap" in names

    def test_zh_bot_custom_vocab_still_applies(self) -> None:
        expander = VocabularyExpander(language="zh")
        bot_vocab = {"shipping": ["运送", "物流"]}
        matches = expander.detect_matches("shipping fee", bot_custom_vocab=bot_vocab)
        assert any(m.original_token == "shipping" for m in matches)


# ---------------------------------------------------------------------------
# 4. Factory cache returns per-language instance
# ---------------------------------------------------------------------------

class TestFactoryPerLanguage:
    def test_factory_caches_per_language(self) -> None:
        a = get_default_expander("vi")
        b = get_default_expander("vi")
        assert a is b

    def test_factory_returns_distinct_for_different_languages(self) -> None:
        vi = get_default_expander("vi")
        en = get_default_expander("en")
        assert vi is not en
        assert vi.language == "vi"
        assert en.language == "en"
        # And the bases differ.
        assert vi._base  # noqa: SLF001 — non-empty
        assert en._base == {}  # noqa: SLF001


# ---------------------------------------------------------------------------
# 5. Explicit base_vocab arg bypasses language gate (DI / system_config path)
# ---------------------------------------------------------------------------

def test_explicit_base_vocab_bypasses_language_gate() -> None:
    # Operator can inject a system_config-loaded dict directly. Even an
    # EN-language expander honours the explicit injection.
    explicit_vocab = {"ko": ["nope"]}
    expander = VocabularyExpander(base_vocab=explicit_vocab, language="en")
    matches = expander.detect_matches("ko go please")
    found = next((m for m in matches if m.original_token == "ko"), None)
    assert found is not None
    assert found.expansions == ["nope"]
