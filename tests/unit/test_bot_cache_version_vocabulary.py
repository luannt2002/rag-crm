"""M19 — cache version must bust on answer-affecting custom_vocabulary edits.

``bot_custom_vocabulary`` (owner-taught synonym map in ``pipeline_config``)
is consumed at retrieval time (``_resolve_stats_keyword_synonyms``) so editing
it changes which chunks reach the LLM, hence the answer. Pre-fix the version
key hashed only (system_prompt, oos_template) so a vocabulary edit left the key
unchanged and the semantic/exact cache served a STALE answer until TTL expiry.

These assert that a vocabulary change moves the version string while an
unrelated input (key order / equivalent-empty) does not.
"""

from __future__ import annotations

import hashlib

from ragbot.orchestration.query_graph_helpers import _compute_bot_cache_version
from ragbot.shared.constants import DEFAULT_BOT_CACHE_VERSION_HASH_LEN


def test_custom_vocabulary_change_busts_cache():
    base = _compute_bot_cache_version(
        "You are a helpful assistant.",
        "Sorry, I cannot help.",
        custom_vocabulary={"synonyms": {"da": ["da chết"]}},
    )
    edited = _compute_bot_cache_version(
        "You are a helpful assistant.",
        "Sorry, I cannot help.",
        custom_vocabulary={"synonyms": {"da": ["da chết", "chăm sóc da"]}},
    )
    assert base != edited


def test_empty_vocabulary_matches_omitted_vocabulary():
    """Backward-compat: no vocab arg ≡ empty/None vocab — keeps legacy keys hot."""
    omitted = _compute_bot_cache_version(
        "You are a helpful assistant.", "Sorry, I cannot help.",
    )
    empty_dict = _compute_bot_cache_version(
        "You are a helpful assistant.", "Sorry, I cannot help.",
        custom_vocabulary={},
    )
    none_vocab = _compute_bot_cache_version(
        "You are a helpful assistant.", "Sorry, I cannot help.",
        custom_vocabulary=None,
    )
    assert omitted == empty_dict == none_vocab
    # Anchor to the EXACT pre-fix 2-field payload ("sp|oos", no trailing pipe) so a
    # vocabulary-less bot keeps its existing cache keys hot — no global cold-cache
    # flush on deploy. Without this anchor the three values above agree trivially
    # (all traverse the new function) and would not catch a trailing-pipe regression.
    sp, oos = "You are a helpful assistant.", "Sorry, I cannot help."
    legacy = hashlib.sha256(f"{sp}|{oos}".encode("utf-8")).hexdigest()[
        :DEFAULT_BOT_CACHE_VERSION_HASH_LEN
    ]
    assert omitted == legacy


def test_vocabulary_key_order_does_not_change_hash():
    """Deterministic serialization — dict key order is not answer-affecting."""
    a = _compute_bot_cache_version(
        "p", "o",
        custom_vocabulary={"synonyms": {"a": ["x"], "b": ["y"]}},
    )
    b = _compute_bot_cache_version(
        "p", "o",
        custom_vocabulary={"synonyms": {"b": ["y"], "a": ["x"]}},
    )
    assert a == b


def test_vocabulary_hash_length_and_determinism():
    out1 = _compute_bot_cache_version(
        "p", "o", custom_vocabulary={"synonyms": {"da": ["da chết"]}},
    )
    out2 = _compute_bot_cache_version(
        "p", "o", custom_vocabulary={"synonyms": {"da": ["da chết"]}},
    )
    assert out1 == out2
    assert len(out1) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN
