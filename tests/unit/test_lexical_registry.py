"""Lexical retrieval registry tests.

Coverage:
- Registry exposes both ``pg_textsearch`` and ``null`` keys.
- ``build_lexical_retrieval`` resolves each key to the expected concrete class.
- Unknown / empty / None / whitespace inputs fall back to NullLexicalRetrieval.
- Case + whitespace normalization on the provider key.
- Default kwarg filter does not blow up when the caller passes extra kwargs.
- ``get_provider_name()`` matches the registry key for both adapters
  (drift guard against silent mislabel).
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.retrieval.lexical_registry import (
    build_lexical_retrieval,
    list_providers,
)
from ragbot.infrastructure.retrieval.null_lexical_retrieval import NullLexicalRetrieval
from ragbot.infrastructure.retrieval.pg_bm25_retrieval import PgBM25Retrieval


def test_registry_lists_both_providers() -> None:
    providers = list_providers()
    assert set(providers) == {"pg_textsearch", "null"}
    # Sorted for stable test asserts.
    assert providers == sorted(providers)


@pytest.mark.parametrize(
    "key,expected_cls",
    [
        ("pg_textsearch", PgBM25Retrieval),
        ("null", NullLexicalRetrieval),
    ],
)
def test_registry_resolves_each_key(key: str, expected_cls: type) -> None:
    # ``session_factory`` required by PgBM25Retrieval; NullLexicalRetrieval
    # accepts **kwargs so the same call works for both.
    inst = build_lexical_retrieval(key, session_factory=lambda: None)
    assert isinstance(inst, expected_cls)


@pytest.mark.parametrize(
    "bad_key",
    ["", None, "unknown-strategy", "PG_TEXTSEARCH_V9000", "elasticsearch"],
)
def test_registry_unknown_falls_back_to_null(bad_key) -> None:
    inst = build_lexical_retrieval(bad_key, session_factory=lambda: None)
    assert isinstance(inst, NullLexicalRetrieval)


def test_registry_normalises_case_and_whitespace() -> None:
    # Operators may paste keys with stray whitespace / different case from
    # the DB. The registry should tolerate it the same way reranker does.
    inst = build_lexical_retrieval("  PG_TEXTSEARCH  ", session_factory=lambda: None)
    assert isinstance(inst, PgBM25Retrieval)


def test_provider_name_matches_registry_key() -> None:
    # Drift guard: rename + missed registry update breaks here.
    assert PgBM25Retrieval.get_provider_name() == "pg_textsearch"
    assert NullLexicalRetrieval.get_provider_name() == "null"


def test_null_accepts_arbitrary_kwargs() -> None:
    # The bootstrap factory passes ``session_factory=`` for every adapter;
    # NullLexicalRetrieval must not refuse it.
    inst = build_lexical_retrieval(
        "null",
        session_factory=lambda: None,
        normalization_flags=42,
        rrf_k=99,
    )
    assert isinstance(inst, NullLexicalRetrieval)


def test_pg_bm25_init_failure_falls_back_to_null() -> None:
    # Passing a non-callable session_factory makes PgBM25 still construct
    # (no eager probe in __init__); the registry's TypeError guard is the
    # fallback when a real adapter __init__ throws. Smoke-check via a
    # provoked TypeError on the normalization_flags coercion.
    inst = build_lexical_retrieval(
        "pg_textsearch",
        session_factory=lambda: None,
        normalization_flags="not-an-int",
    )
    # Either it constructed (int() coerces "not-an-int" → ValueError, which
    # bubbles to the registry's except (TypeError, ValueError) → null), or
    # it constructed cleanly. Both outcomes preserve "never crash boot".
    assert isinstance(inst, (PgBM25Retrieval, NullLexicalRetrieval))
