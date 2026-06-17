"""Unicode normalization helpers — NFC canonical for VN content per CLAUDE.md.

Rationale (P1 NFKC->NFC sweep):
- Vietnamese diacritic best-practice = NFC canonical equivalence (preserves
  exact glyph form, no compatibility folding).
- NFKC over-normalizes: ``"①" -> "1"``, ``"㎏" -> "kg"``,
  halfwidth -> fullwidth — which silently destroys technical Unicode that may
  legitimately appear in VN technical content (e.g. unit symbols, circled
  digits in tables).
- Cache key consistency: corpus stores normalize via ``normalize_for_hash`` and
  query path also normalizes via ``normalize_for_hash`` — both NFC -> hash keys
  match. Mixing NFC ingest with NFKC query (or vice versa) caused silent
  cache miss + retrieval recall hole.

DO NOT swap these helpers back to NFKC without an audit. The hash form is part
of the on-disk cache contract.
"""

from __future__ import annotations

import unicodedata

from ragbot.shared.constants import DEFAULT_NORMALIZATION_FORM


def normalize_vn(text: str) -> str:
    """Canonical VN normalization — NFC.

    Preserves diacritic precise form. No width/halfwidth fold, no compatibility
    decomposition. Use this for any user-visible VN content: chunk text,
    query text, entity strings, keyword search input.
    """
    return unicodedata.normalize(DEFAULT_NORMALIZATION_FORM, text)


def normalize_for_hash(text: str) -> str:
    """Hash-stable canonical — explicit NFC for cache key consistency.

    DO NOT change to NFKC — corpus stores NFC + query NFC must match.
    The hash form is part of the persisted cache contract; switching
    normalization form invalidates every existing hashed row.
    """
    return unicodedata.normalize(DEFAULT_NORMALIZATION_FORM, text)


__all__ = ["normalize_vn", "normalize_for_hash"]
