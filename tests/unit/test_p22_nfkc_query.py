"""P22 VN1 (revised ) — verify NFC normalization of query_text.

Rationale: macOS/iOS/mobile IMEs often emit NFD (decomposed) Vietnamese
characters. The ingest path normalizes to NFC; without symmetric
normalization at query time, NFD queries miss NFC-indexed content.

History:
- P22 originally pinned NFKC on both ingest + query; symmetry was correct
  but NFKC over-normalized VN technical content (circled enumerators,
  unit symbols, fullwidth digits) and introduced silent recall holes.
- hidden-bug audit moved both sides to NFC. Composed-vs-
  decomposed dedup (the macOS/iOS use case this test was written for) is
  served correctly by NFC; only width/compatibility folding is dropped.

These tests verify (a) NFC normalization is effectively applied to query
text, and (b) edge cases (already-NFC, empty string) are safe.
"""
from __future__ import annotations

import inspect
import unicodedata

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


def _normalize(s: str) -> str:
    """Mirror the exact line at the top of hybrid_search body (S13 P1: NFC)."""
    return unicodedata.normalize("NFC", s)


def test_nfd_query_normalized_to_nfc() -> None:
    """NFD 'ắ' (a + U+0306 + U+0301) → single codepoint U+1EAF."""
    nfd = "a" + "̆" + "́"  # a + combining breve + combining acute
    # Sanity: input truly is NFD (3 codepoints), not already composed.
    assert len(nfd) == 3
    assert nfd != "ắ"

    out = _normalize(nfd)

    # NFC composes diacritics → single codepoint "ắ".
    assert out == "ắ"
    assert len(out) == 1


def test_nfc_query_unchanged() -> None:
    """Already-NFC 'ổ' (U+1ED5) stays the same under NFC."""
    nfc = "ổ"  # ổ
    assert len(nfc) == 1
    out = _normalize(nfc)
    assert out == nfc
    assert len(out) == 1


def test_multiple_vn_chars_normalized() -> None:
    """Multi-char NFD string → all chars composed to NFC single codepoints."""
    # NFD forms:
    #   ắ = a + U+0306 + U+0301
    #   ổ = o + U+0302 + U+0309
    #   ụ = u + U+0323
    #   ẫ = a + U+0302 + U+0303
    nfd_string = (
        "ắ "        # ắ
        "ổ "        # ổ
        "ụ "              # ụ
        "ẫ"         # ẫ
    )
    expected = "ắ ổ ụ ẫ"  # ắ ổ ụ ẫ

    out = _normalize(nfd_string)

    assert out == expected
    # Each Vietnamese char is a single codepoint post-normalization.
    for ch in out.split(" "):
        assert len(ch) == 1


def test_empty_string_safe() -> None:
    """Empty string → empty string, no exception."""
    out = _normalize("")
    assert out == ""


def test_hybrid_search_body_normalizes_query() -> None:
    """Guard: the production method actually normalizes query_text via the
    canonical ``normalize_vn`` helper (NFC under S13 P1).

    Protects against accidental removal of the normalization line AND
    against re-introducing inline ``unicodedata.normalize(...)`` (which
    would bypass the shared helper and risk drifting out of sync with
    ingest-path normalization).
    """
    src = inspect.getsource(PgVectorStore.hybrid_search)
    assert "normalize_vn(query_text)" in src, (
        "hybrid_search must call normalize_vn(query_text) for VN-symmetric "
        "retrieval (S13 P1); line missing or altered."
    )
    # Must precede the record_bot_id None-check (i.e., be at top of body).
    nfc_idx = src.index("normalize_vn(query_text)")
    bot_id_idx = src.index("record_bot_id is None")
    assert nfc_idx < bot_id_idx, (
        "Query normalization must run BEFORE any other logic in hybrid_search."
    )
    # Banned: raw NFKC call back-door.
    assert 'unicodedata.normalize("NFKC"' not in src
