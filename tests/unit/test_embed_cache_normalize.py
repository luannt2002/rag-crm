"""EmbedCache key normalization — strip + lower before SHA256.

Verifies that whitespace + case noise collide on the same cache key,
so identical user queries don't miss the cache and re-pay embedding cost.
"""

from __future__ import annotations

from ragbot.infrastructure.cache.embed_cache import EmbedCache


def test_key_normalizes_leading_trailing_whitespace():
    cache = EmbedCache(redis_client=None)
    assert cache._key("hello", model="m") == cache._key(" hello ", model="m")
    assert cache._key("hello", model="m") == cache._key("\thello\n", model="m")


def test_key_normalizes_case():
    cache = EmbedCache(redis_client=None)
    assert cache._key("Hello", model="m") == cache._key("hello", model="m")
    assert cache._key("HELLO", model="m") == cache._key("hello", model="m")


def test_key_combines_strip_and_lower():
    cache = EmbedCache(redis_client=None)
    assert cache._key("  Hello  ", model="m") == cache._key("hello", model="m")


def test_key_distinct_for_different_text():
    cache = EmbedCache(redis_client=None)
    assert cache._key("hello", model="m") != cache._key("hi", model="m")


def test_key_namespaced_per_model():
    cache = EmbedCache(redis_client=None)
    # Same normalized query but different model → different keys.
    assert cache._key("hello", model="a") != cache._key("hello", model="b")


def test_key_handles_empty_query():
    cache = EmbedCache(redis_client=None)
    # Empty after strip — still produces a valid key (no crash).
    k = cache._key("   ", model="m")
    assert k.startswith("ragbot:embed:m:")
