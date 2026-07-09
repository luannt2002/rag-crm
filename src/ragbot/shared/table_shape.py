"""Shape/value column typing (ADR-0008 A1) — decide what a cell/column IS by the
SHAPE of its VALUES, never by a header word list or column position.

This is the deterministic, ZERO-model, ZERO-vocabulary replacement for the
structured-index guessing that lost the product brand (truth-audit 2026-07-07:
``entity_name`` held an internal code ``2-R16 195/55 LPD`` while the real name
``Lốp Rovelo 195/55R16 …`` sat unused → 97% false-deny). It honours the module's
own stated law — "shape, not vocabulary" — that the header/price detectors already
follow but the NAME selection abandoned.

Domain/language-neutral by construction: only punctuation/digit/letter SHAPE is
inspected; no brand, no locale word list, no currency assumption. A price cell is
a price whether its header reads "Giá", "Price", "料金" or nothing.
"""
from __future__ import annotations

import re

from ragbot.shared.tabular_markdown import _is_pure_money

# A "word" = a run of ≥2 letters (Unicode). Digits/underscore excluded, so a code
# token like "R16" or "A68" does NOT count as a descriptive word — only real
# letter-words ("BrandX", "PRODUCTLINE", "Category") do. This is the name/code divider.
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# A size/SKU skeleton: digit-run + separator + digit-run ("195/55", "175-65").
_SIZE_RE = re.compile(r"\d+[/\-]\d+")
# Digits+separators only (a plain number, possibly thousand-separated).
_NUMERIC_RE = re.compile(r"^[\d.,\s]+$")

# A cell carrying this many list separators is an alias/variant blob, never a name.
_LIST_SEP_MIN = 3


def classify_cell_shape(value: str) -> str:
    """Return the SHAPE of one cell value.

    One of: ``empty`` | ``url`` | ``list`` | ``money`` | ``number`` | ``code`` |
    ``name`` | ``token``. Shape only — no vocabulary, no language.
    """
    s = (value or "").strip()
    if not s:
        return "empty"
    if s.startswith(("http://", "https://")):
        return "url"
    if s.count(",") >= _LIST_SEP_MIN or s.count(";") >= _LIST_SEP_MIN:
        return "list"
    if _is_pure_money(s):
        return "money"
    if _NUMERIC_RE.match(s) and any(ch.isdigit() for ch in s):
        return "number"
    n_words = len(_WORD_RE.findall(s))
    # code/identifier: a size or digit-dense token with at most one letter-word
    # ("2-R16 195/55 LPD", "195/55R16", "RCMX+"). Not descriptive.
    if n_words <= 1 and (bool(_SIZE_RE.search(s)) or sum(ch.isdigit() for ch in s) >= 2):
        return "code"
    # descriptive name: two or more letter-words = natural-language free text.
    if n_words >= 2:
        return "name"
    return "token"


def _descriptiveness(value: str) -> int:
    """Higher = more likely to be the human-facing NAME. A ``name``-shaped cell
    scores by word-count then length (fuller descriptions win over stubs); a
    ``code`` is only a weak last resort; everything else cannot be a name."""
    s = (value or "").strip()
    if not s:
        return -1
    shape = classify_cell_shape(s)
    if shape == "name":
        return 1000 + len(_WORD_RE.findall(s)) * 10 + len(s)
    if shape == "code":
        return 1  # fallback only — used when no descriptive candidate exists
    return 0


def pick_descriptive_name(candidates: list[str | None]) -> str | None:
    """Pick the most name-like string among an entity's own field values.

    Replaces the vocab/positional name guess: given every candidate identity field
    (the old code ``entity_name`` + each attribute value), return the one whose
    VALUE SHAPE is the fullest descriptive name. A code loses to a real name; an
    alias blob / number / url can never be the name. Returns None only when no
    non-empty candidate exists (caller keeps its current value).
    """
    best: str | None = None
    best_score = 0
    for c in candidates:
        s = (c or "").strip()
        if not s:
            continue
        score = _descriptiveness(s)
        if score > best_score:
            best, best_score = s, score
    return best


# A query token worth matching against entity identities: a run of ≥3 letters
# (Unicode). Short function words and digit/size tokens are excluded — the size
# code is already the DB keyword; this narrows WITHIN the same-size candidate set.
_QTOK_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def discriminating_token_filter(query: str, cand_texts: list[str]) -> list[int]:
    """Narrow a candidate set by the query's DISCRIMINATING tokens (B3, brand-aware).

    A size-code lookup ("195/55R16") returns every brand of that size; the user
    who asked for "BrandX 195/55R16" must not be served a same-size BrandY.
    For each ≥3-letter query token, if SOME candidates contain it and others do
    NOT, it discriminates (a brand/model word) → keep only those. A token EVERY
    candidate shares (the generic category "Lốp") or NONE contains (grammar words
    "giá"/"bao nhiêu") never filters — so no stopword list and no brand vocabulary
    is needed; the candidate set itself is the dictionary. Returns the surviving
    indices (never empty when input is non-empty — a discriminating step only
    keeps a non-empty subset). ``cand_texts`` = each entity's searchable identity
    text (display name + attributes).
    """
    n = len(cand_texts)
    if n <= 1:
        return list(range(n))
    low = [(t or "").lower() for t in cand_texts]
    toks = {t.lower() for t in _QTOK_RE.findall(query or "")}
    keep = set(range(n))
    for tok in toks:
        have = {i for i in keep if tok in low[i]}
        if 0 < len(have) < len(keep):
            keep = have
    return sorted(keep)


__all__ = [
    "classify_cell_shape",
    "discriminating_token_filter",
    "pick_descriptive_name",
]
