"""Chunk-level Jaccard dedup — drops near-duplicate chunks across docs of one bot."""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Lower-case word tokens for Jaccard similarity (Vietnamese-aware via re.UNICODE)."""
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "")}


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity on word tokens; returns 0.0 when either side is empty."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def find_duplicate_pairs(
    chunks: list[dict[str, Any]],
    *,
    threshold: float,
    min_chars: int,
) -> list[tuple[str, str]]:
    """Return list of (kept_id, drop_id) pairs where Jaccard >= threshold.

    Pair selection: keep older chunk (min created_at), drop newer.
    Skips chunks shorter than min_chars (low-signal headers/orphans).

    Tokenises each candidate once up-front so the pairwise comparison
    is set-intersection only — N-chunk batch costs O(N) tokenisation
    instead of the legacy O(N²) re-tokenise-per-pair.
    """
    candidates = [c for c in chunks if len(c.get("content") or "") >= min_chars]
    tokens = [_tokenize(c["content"]) for c in candidates]
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(candidates):
        ta = tokens[i]
        if not ta:
            continue
        for j in range(i + 1, len(candidates)):
            tb = tokens[j]
            if not tb:
                continue
            inter = len(ta & tb)
            union = len(ta | tb)
            if union == 0:
                continue
            sim = inter / union
            if sim >= threshold:
                b = candidates[j]
                older, newer = (a, b) if a["created_at"] <= b["created_at"] else (b, a)
                pairs.append((str(older["id"]), str(newer["id"])))
    return pairs
