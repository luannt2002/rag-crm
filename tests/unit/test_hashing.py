"""Unit tests for ``shared.hashing``.

Cache-key contract — privacy-preserving SHA-256 of normalized text.

NFC canonical normalization rule () is a hard contract: corpus
ingest + query path must hash to the same digest. These tests pin that
rule + the None / empty / whitespace edge cases.
"""

from __future__ import annotations

import hashlib
import unicodedata

import pytest

from ragbot.shared.hashing import content_hash, content_hash_required


def test_content_hash_returns_hex_digest() -> None:
    h = content_hash("hello world")
    assert h is not None
    # SHA-256 hex
    assert len(h) == 64
    int(h, 16)  # raises if non-hex


def test_content_hash_none_input_returns_none() -> None:
    assert content_hash(None) is None


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t  \n"])
def test_content_hash_blank_returns_none(blank: str) -> None:
    # After strip+lower+normalize the result is empty -> hash returns None
    # (privacy: never hash a sentinel-empty string for variable-presence
    # callers; required-form below has its own contract).
    assert content_hash(blank) is None


def test_content_hash_is_lowercase_normalized() -> None:
    # strip + lower so different surface forms collide.
    a = content_hash("  Hello WORLD  ")
    b = content_hash("hello world")
    assert a == b


def test_content_hash_uses_nfc_canonical_form() -> None:
    # "ế" can be encoded as a single code point (NFC) or as base + combining
    # acute (NFD). The contract is NFC: both forms hash identically.
    nfc = unicodedata.normalize("NFC", "tế bào")
    nfd = unicodedata.normalize("NFD", "tế bào")
    assert nfc != nfd  # sanity: the inputs are actually different bytes
    assert content_hash(nfc) == content_hash(nfd)


def test_content_hash_does_not_kfc_fold_compatibility_chars() -> None:
    # CRITICAL: must use NFC, NOT NFKC. NFKC would fold "①" -> "1" and "㎏" -> "kg",
    # which silently destroys VN technical content. Pin the no-fold contract.
    assert content_hash("①") != content_hash("1")
    assert content_hash("㎏") != content_hash("kg")


def test_content_hash_required_handles_blank_input() -> None:
    # required variant always returns a hash — even for empty string.
    h = content_hash_required("")
    assert h is not None
    # Independently computable: hash of normalized empty string.
    expected = hashlib.sha256(b"").hexdigest()
    assert h == expected


def test_content_hash_required_matches_content_hash_for_real_text() -> None:
    text = "Some text"
    a = content_hash(text)
    b = content_hash_required(text)
    assert a == b
