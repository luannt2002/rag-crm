"""Privacy-preserving hashing helpers (Phần 2.B — GDPR)."""

from __future__ import annotations

import hashlib

from ragbot.shared.text_normalization import normalize_for_hash


def _normalize(text: str) -> str:
    # NFC canonical; cache rebuild required when switching normalisation forms.
    return normalize_for_hash(text.strip().lower())


def content_hash(text: str | None) -> str | None:
    """SHA-256 hex of normalized text. Returns None if input is None/empty."""
    if text is None:
        return None
    norm = _normalize(text)
    if not norm:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def content_hash_required(text: str) -> str:
    """SHA-256 hex — empty-string safe (returns hash of empty string if blank)."""
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


__all__ = ["content_hash", "content_hash_required"]
