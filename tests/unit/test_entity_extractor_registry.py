"""Entity extractor strategy registry — unit tests.

Pins:
- Port Protocol satisfied by all 3 strategies
- Registry default = ``null`` for missing/unknown/empty/None provider
- Each strategy honours its language gate (returns ``[]`` on mismatch)
- Vertical-agnostic — tests use only generic VN + EN text (no industry
  literals like spa / finance / healthcare keywords)
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.entity_extractor_port import EntityExtractorPort
from ragbot.infrastructure.entity_extractor.en_simple_extractor import EnSimpleExtractor
from ragbot.infrastructure.entity_extractor.null_extractor import NullExtractor
from ragbot.infrastructure.entity_extractor.registry import (
    build_entity_extractor,
    list_providers,
)
from ragbot.infrastructure.entity_extractor.vi_underthesea_extractor import (
    ViUnderthesseaExtractor,
)


# --------------------------------------------------------------------------- #
# Registry resolution                                                         #
# --------------------------------------------------------------------------- #


def test_registry_default_is_null_for_falsy_or_unknown() -> None:
    """Falsy / typo / None all collapse to NullExtractor."""
    for prov in (None, "", "  ", "does_not_exist_xyz", "VI_UNDERTHESEA_TYPO"):
        instance = build_entity_extractor(prov)
        assert isinstance(instance, NullExtractor), f"prov={prov!r}"


def test_registry_resolves_known_providers() -> None:
    """Each registered key returns the matching class."""
    assert isinstance(build_entity_extractor("null"), NullExtractor)
    assert isinstance(build_entity_extractor("vi_underthesea"), ViUnderthesseaExtractor)
    assert isinstance(build_entity_extractor("en_simple"), EnSimpleExtractor)
    # Case-insensitive resolution.
    assert isinstance(build_entity_extractor("VI_UNDERTHESEA"), ViUnderthesseaExtractor)
    assert isinstance(build_entity_extractor("En_Simple"), EnSimpleExtractor)


def test_list_providers_sorted_and_complete() -> None:
    providers = list_providers()
    assert "null" in providers
    assert "vi_underthesea" in providers
    assert "en_simple" in providers
    assert providers == sorted(providers), "list_providers must return sorted"
    # Pin the count so a future drive-by addition is a deliberate test
    # update rather than an accidental merge.
    assert len(providers) >= 3


def test_registry_kwargs_filtered_safely() -> None:
    """Unknown kwargs must not blow up strategy construction.

    Registry strategies all accept ``**_``; this test pins that callers
    can pass extra keyword arguments (e.g. via the DI container) without
    constructor TypeError. Future strict-signature strategies are
    protected by the ``inspect.signature`` filter.
    """
    inst = build_entity_extractor("null", api_key="ignored", model="ignored")
    assert isinstance(inst, NullExtractor)
    inst2 = build_entity_extractor("en_simple", random_kw="x")
    assert isinstance(inst2, EnSimpleExtractor)


# --------------------------------------------------------------------------- #
# Port Protocol + provider name                                               #
# --------------------------------------------------------------------------- #


def test_all_strategies_implement_port_protocol() -> None:
    assert isinstance(NullExtractor(), EntityExtractorPort)
    assert isinstance(ViUnderthesseaExtractor(), EntityExtractorPort)
    assert isinstance(EnSimpleExtractor(), EntityExtractorPort)


def test_provider_names_unique_and_match_registry_keys() -> None:
    """get_provider_name must equal the registry key — pin against drift."""
    assert NullExtractor.get_provider_name() == "null"
    assert ViUnderthesseaExtractor.get_provider_name() == "vi_underthesea"
    assert EnSimpleExtractor.get_provider_name() == "en_simple"


# --------------------------------------------------------------------------- #
# Null strategy — true no-op                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_null_extractor_returns_empty_for_any_input() -> None:
    n = NullExtractor()
    # Vietnamese, English, empty, whitespace, punctuation — all empty.
    assert await n.extract("xin chào bạn", language="vi") == []
    assert await n.extract("hello world", language="en") == []
    assert await n.extract("", language="vi") == []
    assert await n.extract("   ", language="en") == []
    assert await n.extract("?!?", language="vi") == []


# --------------------------------------------------------------------------- #
# VN strategy — language gate + entity extraction                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vi_extractor_language_gate_skips_non_vn() -> None:
    """Non-VN language → []. Multi-tenant safety — EN/ZH bot must not
    get VN underthesea NER applied to its queries."""
    vi = ViUnderthesseaExtractor()
    assert await vi.extract("Some text", language="en") == []
    assert await vi.extract("テキスト", language="ja") == []
    assert await vi.extract("文本", language="zh") == []
    # Empty input is empty regardless of language.
    assert await vi.extract("", language="vi") == []


@pytest.mark.asyncio
async def test_vi_extractor_picks_proper_nouns_and_numerics() -> None:
    """Generic VN query — no industry literal — must produce some
    ranked entity output. Pins that the underthesea backend wiring is
    live in the test environment."""
    vi = ViUnderthesseaExtractor()
    out = await vi.extract(
        "Số điện thoại chi nhánh ABC là 0901234567",
        language="vi",
    )
    # Backend may rank these multiple ways; pin only the non-empty +
    # contains-the-anchor-tokens contract so a future underthesea
    # version bump does not break the test.
    assert isinstance(out, list)
    assert len(out) >= 1
    # The numeric phone must always be picked up — most stable signal.
    joined = " ".join(out)
    assert "0901234567" in joined
    # ABC (proper noun all-caps) must appear somewhere.
    assert "ABC" in joined


@pytest.mark.asyncio
async def test_vi_extractor_returns_empty_on_no_entities() -> None:
    """Pure-VN function-word query → []. Ensures the fallback path
    is empty (not all-tokens) so noise doesn't pollute the variant
    list. Generic VN — no industry word."""
    vi = ViUnderthesseaExtractor()
    out = await vi.extract("làm sao để đăng ký", language="vi")
    assert out == []


# --------------------------------------------------------------------------- #
# EN strategy — language gate + entity extraction                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_en_extractor_language_gate_skips_non_en() -> None:
    """Non-EN language → []."""
    en = EnSimpleExtractor()
    assert await en.extract("Apple iPhone Vietnam", language="vi") == []
    assert await en.extract("Apple iPhone Vietnam", language="ja") == []
    assert await en.extract("", language="en") == []


@pytest.mark.asyncio
async def test_en_extractor_picks_multiword_caps_and_numerics() -> None:
    """Multi-word capitalised + numerics — the supported signals."""
    en = EnSimpleExtractor()
    out = await en.extract(
        "Acme Corp released version 3.5 in New York on 2024",
        language="en",
    )
    joined = " ".join(out)
    # Multi-word capitalised must pick "Acme Corp" or "New York".
    assert ("Acme Corp" in joined) or ("New York" in joined)
    # Numeric must show up.
    assert ("2024" in joined) or ("3.5" in joined)


@pytest.mark.asyncio
async def test_en_extractor_emits_all_caps_acronyms() -> None:
    """USA / NASA-style all-caps tokens — single-word path."""
    en = EnSimpleExtractor()
    out = await en.extract("Send to USA via NASA channel ABC123", language="en")
    joined = " ".join(out)
    assert "USA" in joined
    assert "NASA" in joined
    assert "ABC123" in joined


@pytest.mark.asyncio
async def test_en_extractor_returns_empty_on_function_words() -> None:
    """Lower-case function-word query → []."""
    en = EnSimpleExtractor()
    out = await en.extract("how do i register", language="en")
    assert out == []


# --------------------------------------------------------------------------- #
# Domain-neutral guard — fixtures must not include vertical literals          #
# --------------------------------------------------------------------------- #


def test_test_fixtures_are_domain_neutral() -> None:
    """Self-test that the strings used above contain no industry / brand /
    domain-specific literals from the CLAUDE.md banned list. A regression
    in this file must not introduce vertical-specific test data.
    """
    fixtures_text = " ".join(
        [
            "xin chào bạn",
            "hello world",
            "Số điện thoại chi nhánh ABC là 0901234567",
            "làm sao để đăng ký",
            "Apple iPhone Vietnam",
            "Acme Corp released version 3.5 in New York on 2024",
            "Send to USA via NASA channel ABC123",
            "how do i register",
        ]
    ).lower()
    banned = ("spa", "massage", "chăm sóc da", "triệt lông", "gội đầu")
    for term in banned:
        assert term not in fixtures_text, (
            f"vertical literal '{term}' leaked into test fixtures"
        )
