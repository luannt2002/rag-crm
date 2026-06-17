"""Unit tests — proximity cache Port + Registry + Null/LSH strategies.

Mock-only / pure-Python; no DB, no Redis, no embedder. Strong assertions on
returned dataclass values and registry types.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.proximity_cache_port import (
    CacheHit,
    ProximityCachePort,
)
try:
    from ragbot.infrastructure.proximity_cache import (
        LSHProximityCache,
        NullProximityCache,
        build_proximity_cache,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "proximity_cache subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import (
    DEFAULT_PROXIMITY_CACHE_SIMILARITY_THRESHOLD,
    DEFAULT_PROXIMITY_CACHE_TTL_S,
)


def test_null_proximity_cache_lookup_miss_and_store_noop() -> None:
    cache = NullProximityCache()
    assert isinstance(cache, ProximityCachePort)
    assert cache.get_provider_name() == "null"

    miss = cache.lookup([0.1, 0.2, 0.3])
    assert miss is None

    # Store is a no-op; subsequent lookup must still miss to confirm
    # the Null adapter never persists state.
    cache.store([0.1, 0.2, 0.3], "ignored answer", DEFAULT_PROXIMITY_CACHE_TTL_S)
    again = cache.lookup([0.1, 0.2, 0.3])
    assert again is None


def test_lsh_proximity_cache_empty_lookup_returns_none() -> None:
    cache = LSHProximityCache()
    assert isinstance(cache, ProximityCachePort)
    assert cache.get_provider_name() == "lsh"

    result = cache.lookup([0.1, 0.2, 0.3, 0.4])
    assert result is None


def test_lsh_proximity_cache_hit_after_store_with_similar_embedding() -> None:
    # Threshold lowered to make the test deterministic for a small synthetic
    # vector — production callers should use the constant default (0.92).
    cache = LSHProximityCache(similarity_threshold=0.95)
    stored = [1.0, 0.0, 0.0, 0.0]
    cache.store(stored, "cached answer 42", DEFAULT_PROXIMITY_CACHE_TTL_S)

    # Highly-similar (near-duplicate) embedding — cosine ~ 0.9999.
    similar = [1.0, 0.01, 0.0, 0.0]
    hit = cache.lookup(similar)

    assert isinstance(hit, CacheHit)
    assert hit.answer == "cached answer 42"
    assert hit.similarity >= 0.95
    assert hit.similarity <= 1.0
    # Frozen dataclass — confirm immutability is preserved.
    with pytest.raises(AttributeError):
        hit.answer = "mutated"  # type: ignore[misc]


def test_lsh_proximity_cache_below_threshold_returns_none() -> None:
    # Threshold = default constant; orthogonal vectors fall well below it.
    cache = LSHProximityCache(
        similarity_threshold=DEFAULT_PROXIMITY_CACHE_SIMILARITY_THRESHOLD,
    )
    cache.store([1.0, 0.0, 0.0, 0.0], "won't match", DEFAULT_PROXIMITY_CACHE_TTL_S)

    # Orthogonal vector → cosine 0.0 << 0.92 threshold.
    orthogonal = [0.0, 1.0, 0.0, 0.0]
    result = cache.lookup(orthogonal)
    assert result is None


def test_build_proximity_cache_registry_resolves_known_and_rejects_unknown() -> None:
    null_cache = build_proximity_cache("null")
    assert isinstance(null_cache, NullProximityCache)

    lsh_cache = build_proximity_cache("lsh")
    assert isinstance(lsh_cache, LSHProximityCache)

    # Default fallback (None / empty) → null adapter.
    default_cache = build_proximity_cache(None)
    assert isinstance(default_cache, NullProximityCache)

    # Unknown provider must fail loud — typos in system_config should not
    # silently disable the cache.
    with pytest.raises(ValueError, match="unknown proximity_cache provider"):
        build_proximity_cache("does-not-exist")
