"""Semantic-cache bot_version derivation tests.

Bot owner edits to ``system_prompt`` or ``oos_answer_template`` must bump
the cache key so users do not receive stale answers until TTL expiry.
"""

from __future__ import annotations

from ragbot.orchestration.query_graph import _compute_bot_cache_version
from ragbot.shared.constants import DEFAULT_BOT_CACHE_VERSION_HASH_LEN


def test_same_inputs_produce_same_hash():
    a = _compute_bot_cache_version("You are a helpful assistant.", "Sorry, I cannot help.")
    b = _compute_bot_cache_version("You are a helpful assistant.", "Sorry, I cannot help.")
    assert a == b


def test_system_prompt_change_busts_cache():
    base = _compute_bot_cache_version("You are a helpful assistant.", "Sorry, I cannot help.")
    edited = _compute_bot_cache_version("You are a friendly assistant.", "Sorry, I cannot help.")
    assert base != edited


def test_oos_template_change_busts_cache():
    base = _compute_bot_cache_version("You are a helpful assistant.", "Sorry, I cannot help.")
    edited = _compute_bot_cache_version("You are a helpful assistant.", "Out of scope.")
    assert base != edited


def test_none_inputs_are_stable_and_do_not_crash():
    a = _compute_bot_cache_version(None, None)
    b = _compute_bot_cache_version("", "")
    # None and "" are equivalent under the helper contract.
    assert a == b
    assert len(a) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN


def test_hash_length_matches_constant():
    out = _compute_bot_cache_version("prompt-x", "oos-y")
    assert len(out) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN


def test_determinism_across_repeated_calls():
    payloads = [
        ("p1", "o1"),
        ("longer system prompt with multiple lines\nincluding newlines", "refusal text"),
        ("", ""),
    ]
    for sp, oos in payloads:
        first = _compute_bot_cache_version(sp, oos)
        for _ in range(5):
            assert _compute_bot_cache_version(sp, oos) == first


def test_separator_prevents_boundary_collision():
    """Concatenating fields without a separator could collide; the helper uses '|'."""
    a = _compute_bot_cache_version("ab", "c")
    b = _compute_bot_cache_version("a", "bc")
    assert a != b
