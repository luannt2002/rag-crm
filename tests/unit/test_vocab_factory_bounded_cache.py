"""``get_default_expander`` and ``get_enricher_for_language`` cache one
instance per language. The original implementation used an unbounded
``dict``, so a tenant supplying a thousand distinct language tags
would retain a thousand instances for the lifetime of the worker.

Both factories now use ``functools.lru_cache(maxsize=N)`` so the
working set stays bounded, while the practical case (a handful of
languages per deployment) keeps its O(1) hit path.
"""

from __future__ import annotations

from ragbot.application.services import (
    superlative_context_enricher as sup_mod,
    vocabulary_expander as vocab_mod,
)


def test_get_default_expander_is_cached_for_known_language() -> None:
    a = vocab_mod.get_default_expander("vi")
    b = vocab_mod.get_default_expander("vi")
    assert a is b, "same language must return the same expander instance"


def test_get_default_expander_cache_is_bounded() -> None:
    info = vocab_mod.get_default_expander.cache_info()  # type: ignore[attr-defined]
    assert info.maxsize is not None and info.maxsize <= 256, (
        "expander cache must be bounded; lru_cache() with no maxsize would "
        "let an attacker-supplied language tag stream pin one instance per "
        "tag for the lifetime of the worker"
    )


def test_get_enricher_for_language_is_cached() -> None:
    a = sup_mod.get_enricher_for_language("vi")
    b = sup_mod.get_enricher_for_language("vi")
    assert a is b


def test_get_enricher_cache_is_bounded() -> None:
    info = sup_mod.get_enricher_for_language.cache_info()  # type: ignore[attr-defined]
    assert info.maxsize is not None and info.maxsize <= 256


def test_distinct_languages_share_cache_within_capacity() -> None:
    sup_mod.get_enricher_for_language.cache_clear()  # type: ignore[attr-defined]
    sup_mod.get_enricher_for_language("vi")
    sup_mod.get_enricher_for_language("en")
    info = sup_mod.get_enricher_for_language.cache_info()  # type: ignore[attr-defined]
    assert info.currsize == 2
    assert info.misses == 2

    sup_mod.get_enricher_for_language("vi")  # hit
    info2 = sup_mod.get_enricher_for_language.cache_info()  # type: ignore[attr-defined]
    assert info2.hits >= 1
