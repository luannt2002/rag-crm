"""Unit tests: Vietnamese tokenizer — abbreviations, diacritics."""
from __future__ import annotations

import pytest

from ragbot.shared.vi_tokenizer import (
    expand_abbreviations,
    remove_diacritics,
    restore_diacritics,
)


# ── expand_abbreviations ────────────────────────────────────────────────


class TestExpandAbbreviations:
    def test_ko_to_khong(self):
        assert "không" in expand_abbreviations("ko biết")

    def test_bhxh_to_full(self):
        result = expand_abbreviations("bhxh là gì")
        assert "bảo hiểm xã hội" in result

    def test_empty_string_returns_empty(self):
        assert expand_abbreviations("") == ""

    def test_no_match_unchanged(self):
        original = "hello world xin chào"
        assert expand_abbreviations(original) == original

    def test_case_insensitive(self):
        result = expand_abbreviations("KO biết")
        assert "không" in result.lower()

    def test_custom_dict_override(self):
        """Custom dict merges over defaults."""
        result = expand_abbreviations("xyz test", abbrev_dict={"xyz": "replaced"})
        assert "replaced" in result

    def test_whitespace_only_returns_as_is(self):
        assert expand_abbreviations("   ") == "   "

    def test_multiple_abbreviations_in_one_sentence(self):
        result = expand_abbreviations("ko dc ntn")
        assert "không" in result
        assert "được" in result
        assert "như thế nào" in result


# ── remove_diacritics ──────────────────────────────────────────────────


class TestRemoveDiacritics:
    def test_goi_dau(self):
        result = remove_diacritics("gội đầu")
        # NFKD strips combining marks but đ (U+0111, d-stroke) is not decomposable
        assert result == "goi đau"

    def test_empty_string(self):
        assert remove_diacritics("") == ""

    def test_ascii_unchanged(self):
        assert remove_diacritics("hello world") == "hello world"

    def test_mixed_text(self):
        result = remove_diacritics("triệt lông")
        assert result == "triet long"


# ── restore_diacritics ─────────────────────────────────────────────────


class TestRestoreDiacritics:
    @pytest.mark.asyncio
    async def test_with_custom_map(self):
        """restore_diacritics uses custom_map to restore accent-free text."""
        custom = {"bao nhieu": "bao nhiêu", "dich vu": "dịch vụ"}
        result = await restore_diacritics("bao nhieu", custom_map=custom)
        assert result == "bao nhiêu"

    @pytest.mark.asyncio
    async def test_empty_string(self):
        result = await restore_diacritics("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_map_returns_unchanged(self):
        """With empty _DIACRITIC_MAP and no custom_map, text is unchanged."""
        result = await restore_diacritics("bao nhieu")
        assert result == "bao nhieu"

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_as_is(self):
        result = await restore_diacritics("   ")
        assert result == "   "

    @pytest.mark.asyncio
    async def test_use_model_true_falls_back_to_rules(self):
        """No viable VN accent ML pkg on PyPI in 2026 — use_model=True must
        transparently fall back to the rule-based custom_map path so callers
        can flip the config flag without breaking. Lock that contract."""
        custom = {"khong dau": "không dấu", "triet long": "triệt lông"}
        # use_model=True with a custom_map should still apply the rules.
        result = await restore_diacritics(
            "khong dau", use_model=True, custom_map=custom,
        )
        assert result == "không dấu"
        # Mixed: only mapped phrases get restored, the rest is left alone.
        result2 = await restore_diacritics(
            "triet long nach", use_model=True, custom_map=custom,
        )
        assert result2 == "triệt lông nach"

    @pytest.mark.asyncio
    async def test_use_model_true_no_map_returns_unchanged(self):
        """use_model=True with no custom_map and empty built-in map → no-op."""
        result = await restore_diacritics("khong dau", use_model=True)
        assert result == "khong dau"

    @pytest.mark.asyncio
    async def test_already_accented_input_preserved(self):
        """Already-accented text must not be double-mangled by the rule path."""
        custom = {"khong": "không"}
        result = await restore_diacritics("không dấu", custom_map=custom)
        assert result == "không dấu"

    @pytest.mark.asyncio
    async def test_mixed_ascii_and_accented(self):
        """Partially-accented input: only accent-free tokens that match the
        map get restored; accented tokens are passed through verbatim."""
        custom = {"khong": "không", "dau": "dấu"}
        # "không dau" → "không dấu" (only "dau" is in the map AND accent-free)
        result = await restore_diacritics("không dau", custom_map=custom)
        assert result == "không dấu"

    @pytest.mark.asyncio
    async def test_non_vietnamese_input_unchanged(self):
        """Email / code / English text must not be touched by the map."""
        custom = {"khong": "không"}
        for noisy in ("user@example.com", "SELECT * FROM t", "hello world"):
            assert await restore_diacritics(noisy, custom_map=custom) == noisy
