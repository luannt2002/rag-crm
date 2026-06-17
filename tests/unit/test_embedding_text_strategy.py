"""Embedding-text strategy registry — unit tests.

Pins:
- Port Protocol satisfied by every strategy
- Registry default = ``null`` (pass-through prefix+raw) for missing / unknown
- ``prefix_plus_raw`` returns "{prefix}\\n\\n{raw}"
- ``raw_only`` returns ``raw_chunk`` only (LLM prefix discarded)
- Empty / None ``enriched_prefix`` collapses to raw chunk on all strategies
- Domain-neutral fixtures — no brand / industry literals
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.embedding_text_port import EmbeddingTextStrategyPort
from ragbot.infrastructure.embedding_text.null_embedding_text_strategy import (
    NullEmbeddingTextStrategy,
)
from ragbot.infrastructure.embedding_text.prefix_plus_raw_strategy import (
    PrefixPlusRawStrategy,
)
from ragbot.infrastructure.embedding_text.raw_only_strategy import RawOnlyStrategy
from ragbot.infrastructure.embedding_text.registry import (
    build_embedding_text_strategy,
    list_providers,
)


# --------------------------------------------------------------------------- #
# Registry resolution                                                         #
# --------------------------------------------------------------------------- #


def test_registry_default_for_falsy_or_unknown_is_null_strategy() -> None:
    """None / empty / unknown key → NullEmbeddingTextStrategy (warn-only)."""
    for prov in (None, "", "  ", "does_not_exist_xyz"):
        instance = build_embedding_text_strategy(prov)
        assert isinstance(instance, NullEmbeddingTextStrategy), f"prov={prov!r}"


def test_registry_resolves_known_providers() -> None:
    """Each registered key returns the matching class."""
    assert isinstance(
        build_embedding_text_strategy("prefix_plus_raw"),
        PrefixPlusRawStrategy,
    )
    assert isinstance(
        build_embedding_text_strategy("raw_only"),
        RawOnlyStrategy,
    )
    assert isinstance(
        build_embedding_text_strategy("null"),
        NullEmbeddingTextStrategy,
    )


def test_registry_is_case_insensitive() -> None:
    """Operators may type 'RAW_ONLY' — the registry must normalise."""
    assert isinstance(
        build_embedding_text_strategy("RAW_ONLY"),
        RawOnlyStrategy,
    )
    assert isinstance(
        build_embedding_text_strategy("Prefix_Plus_Raw"),
        PrefixPlusRawStrategy,
    )


def test_list_providers_sorted_and_complete() -> None:
    providers = list_providers()
    assert "prefix_plus_raw" in providers
    assert "raw_only" in providers
    assert "null" in providers
    assert providers == sorted(providers), "list_providers must return sorted"
    # Pin the count so a drive-by addition is a deliberate test update.
    assert len(providers) == 3


# --------------------------------------------------------------------------- #
# Port Protocol                                                               #
# --------------------------------------------------------------------------- #


def test_all_strategies_implement_port_protocol() -> None:
    """Every strategy MUST satisfy the runtime_checkable Protocol."""
    assert isinstance(NullEmbeddingTextStrategy(), EmbeddingTextStrategyPort)
    assert isinstance(PrefixPlusRawStrategy(), EmbeddingTextStrategyPort)
    assert isinstance(RawOnlyStrategy(), EmbeddingTextStrategyPort)


def test_strategy_name_matches_registry_key() -> None:
    """`.name` property MUST equal the registry key — pin against drift."""
    assert PrefixPlusRawStrategy().name == "prefix_plus_raw"
    assert RawOnlyStrategy().name == "raw_only"
    assert NullEmbeddingTextStrategy().name == "null"


# --------------------------------------------------------------------------- #
# PrefixPlusRawStrategy — legacy default                                      #
# --------------------------------------------------------------------------- #


def test_prefix_plus_raw_concatenates_with_blank_line() -> None:
    strategy = PrefixPlusRawStrategy()
    out = strategy.build(
        raw_chunk="alpha body text",
        enriched_prefix="summary line one",
    )
    assert out == "summary line one\n\nalpha body text"


def test_prefix_plus_raw_empty_prefix_returns_raw_only() -> None:
    """Empty prefix → raw chunk only (no leading blank line)."""
    strategy = PrefixPlusRawStrategy()
    assert strategy.build(raw_chunk="beta", enriched_prefix="") == "beta"
    assert strategy.build(raw_chunk="gamma", enriched_prefix=None) == "gamma"
    assert strategy.build(raw_chunk="delta", enriched_prefix="   ") == "delta"


# --------------------------------------------------------------------------- #
# RawOnlyStrategy — the fix                                                   #
# --------------------------------------------------------------------------- #


def test_raw_only_discards_prefix_entirely() -> None:
    """`raw_only` MUST NOT leak any byte of the enriched prefix."""
    strategy = RawOnlyStrategy()
    out = strategy.build(
        raw_chunk="alpha body text",
        enriched_prefix="summary that would dilute embedding",
    )
    assert out == "alpha body text"
    # Sanity: not a single token from the prefix bleeds through.
    assert "summary" not in out
    assert "dilute" not in out


def test_raw_only_handles_none_prefix_gracefully() -> None:
    strategy = RawOnlyStrategy()
    assert strategy.build(raw_chunk="epsilon", enriched_prefix=None) == "epsilon"
    assert strategy.build(raw_chunk="zeta", enriched_prefix="") == "zeta"


def test_raw_only_preserves_raw_chunk_byte_for_byte() -> None:
    """Embedding hash stability: raw chunk MUST pass through unchanged."""
    strategy = RawOnlyStrategy()
    raw = "Line one with newline\nand a second line containing digits 12345."
    assert strategy.build(raw_chunk=raw, enriched_prefix="anything") == raw


# --------------------------------------------------------------------------- #
# Null pass-through                                                           #
# --------------------------------------------------------------------------- #


def test_null_strategy_matches_prefix_plus_raw_behaviour() -> None:
    """Null is a fail-soft alias of prefix_plus_raw — same outputs."""
    null_s = NullEmbeddingTextStrategy()
    legacy = PrefixPlusRawStrategy()
    for raw, prefix in (
        ("body", "context"),
        ("body", ""),
        ("body", None),
    ):
        assert null_s.build(raw_chunk=raw, enriched_prefix=prefix) == \
            legacy.build(raw_chunk=raw, enriched_prefix=prefix)


# --------------------------------------------------------------------------- #
# Domain-neutral fixture guard                                                #
# --------------------------------------------------------------------------- #


def test_fixtures_are_domain_neutral() -> None:
    """Self-test that fixture strings carry no banned vertical literals."""
    fixtures_text = " ".join(
        [
            "alpha body text", "beta", "gamma", "delta", "epsilon", "zeta",
            "summary line one", "summary that would dilute embedding",
            "Line one with newline", "context",
        ]
    ).lower()
    banned = ("spa", "massage", "voucher", "medispa")
    for term in banned:
        assert term not in fixtures_text, (
            f"vertical literal '{term}' leaked into test fixtures"
        )
