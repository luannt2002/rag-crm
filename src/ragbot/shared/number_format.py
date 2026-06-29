"""[T1-Smartness] Canonical numeric / money normalization — SINGLE SOURCE OF TRUTH.

Used by BOTH the ingest side (``document_stats`` corpus price extraction) and
the query side (``query_range_parser`` range / superlative parsing). Centralising
here guarantees the corpus and the user query agree on what ``1.200.000`` or
``700,000`` means — otherwise a range filter built from a query (price_max=700)
never matches a price stored from the corpus (700000) and the bot silently
mis-answers numeric questions.

Domain-neutral · language-agnostic for digits · HALLU=0 (pure deterministic
regex, never an LLM). Multi-tenant safe: same rules for every bot.

══════════════════════════════════════════════════════════════════════════
NUMBER STANDARD (the "quy chuẩn số" — applies platform-wide)
══════════════════════════════════════════════════════════════════════════
Thousand separator : '.'  ','  or a space, in groups of EXACTLY 3 digits.
Decimal separator  : the separator followed by 1–2 digits, OR any single
                     separator when a unit suffix (k / tr / triệu / tỷ / M …)
                     follows the number.

Disambiguation of a SINGLE separator with NO unit suffix:
    • exactly 3 digits after  → THOUSANDS   "1.000"=1000  "700,000"=700000
    • 1–2 digits after        → DECIMAL     "1.5"=1.5     "1,22"=1.22
Multiple SAME separators      → THOUSANDS   "1.200.000"=1200000
Both '.' and ','              → the LAST-occurring is the decimal point,
                                the other is the thousands separator:
                                "1.234.567,89"=1234567.89  "1,234,567.89"=1234567.89

Unit suffix multipliers       : tỷ=1e9 · triệu/tr/M=1e6 · nghìn/ngàn/k=1e3 · đồng=1
VN compound                   : "1tr499" = 1·1e6 + 499·1e3 = 1,499,000
                                "1.5tr"  = 1,500,000   "2tr" = 2,000,000

``parse_money_vn`` returns an integer VND amount (fractional đồng do not exist)
or ``None`` when no numeric token is present / the value is below ``min_value``.
``min_value`` lets the ingest side reject ordinal/SKU numbers (e.g. row index 3)
while the query side keeps small numbers it has already context-guarded.
"""
from __future__ import annotations

import re
from typing import Final

from ragbot.shared.constants import DEFAULT_NUMERIC_COVERAGE_MIN_DIGITS

# ---------------------------------------------------------------------------
# Suffix multipliers — diacritic-folded keys (matched case-insensitively).
# ---------------------------------------------------------------------------
_SUFFIX_MULT: Final[dict[str, int]] = {
    "ty": 1_000_000_000,
    "trieu": 1_000_000,
    "tr": 1_000_000,
    "m": 1_000_000,        # English sheets: "1M" / "1.5M"
    "nghin": 1_000,
    "ngan": 1_000,
    "k": 1_000,
    "dong": 1,
}

_DIACRITIC_FOLD: Final[dict[int, str]] = {
    ord("đ"): "d", ord("ư"): "u", ord("ơ"): "o", ord("ă"): "a", ord("â"): "a",
    ord("ê"): "e", ord("ô"): "o", ord("ỷ"): "y", ord("ỳ"): "y", ord("ý"): "y",
    ord("ì"): "i", ord("ĩ"): "i", ord("ị"): "i", ord("ề"): "e", ord("ệ"): "e",
}


def _fold(s: str) -> str:
    """Lower-case + strip the few diacritics that appear in money suffixes."""
    return s.lower().translate(_DIACRITIC_FOLD)


# A leading '-' marks a negative amount — never a valid price. Every pattern
# below uses a negative look-behind on '-' so "-500000" yields no money token.

# VN "tr" compound: "1tr499" / "1.5tr" / "2 tr".  Group 2 = trailing nghìn part.
_TR_COMPOUND_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.,\-])(\d+(?:[.,]\d+)?)\s*tr(\d{1,3})?(?![\w])",
    re.IGNORECASE,
)

# number + unit suffix: "1,5 triệu" / "500k" / "1M" / "2 tỷ" / "800 nghìn".
_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.,\-])(\d[\d.,\s]*?)\s*"
    r"(tỷ|triệu|tr|M|nghìn|ngàn|k|đồng)(?![\w])",
    re.IGNORECASE,
)

# bare or grouped number: "1.200.000" / "700,000" / "1234567" / "1.5" / "1,22".
# Look-behind rejects mid-run starts and negative-signed numbers.
_NUMERIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\d.,\-])(?:\d[\d.,]*\d|\d)"
)


def _normalize_literal(token: str, *, suffix_context: bool = False) -> float | None:
    """Convert a numeric token to a float per the platform NUMBER STANDARD.

    @param token: digits with optional '.' / ',' / space separators.
    @param suffix_context: True when a unit suffix follows (forces the single
        separator to be read as a decimal point, e.g. "1,5 triệu" → 1.5).
    @return float value, or None when the token is not a clean number.
    """
    tok = token.strip().replace(" ", "")
    if not tok or not re.fullmatch(r"\d[\d.,]*\d|\d", tok):
        return None

    dots = tok.count(".")
    commas = tok.count(",")

    # Both separators present → last-occurring one is the decimal point.
    if dots and commas:
        cut = max(tok.rfind("."), tok.rfind(","))
        int_part = re.sub(r"[.,]", "", tok[:cut])
        frac_part = tok[cut + 1:]
        if not (int_part.isdigit() and frac_part.isdigit()):
            return None
        return float(f"{int_part}.{frac_part}")

    sep = "." if dots else ("," if commas else "")
    if not sep:
        return float(tok)

    parts = tok.split(sep)
    # Multiple identical separators → grouped thousands ("1.200.000").
    if len(parts) > 2:
        if all(p.isdigit() for p in parts):
            return float("".join(parts))
        return None

    before, after = parts
    if not (before.isdigit() and after.isdigit()):
        return None
    # With a unit suffix, a single separator is ALWAYS the decimal point.
    if suffix_context:
        return float(f"{before}.{after}")
    # No suffix: exactly 3 trailing digits → thousands; else decimal.
    if len(after) == 3:
        return float(before + after)
    return float(f"{before}.{after}")


def _guard(value: int, min_value: int, max_value: int | None = None) -> int | None:
    """Return value when in [min_value, max_value], else None.

    Rejects ordinal/SKU numbers below the floor AND implausibly-large numbers
    above the ceiling — a 13-digit date like ``2025122435548`` (a Google-Sheet
    timestamp leaking into a price column) is not a price; the ceiling keeps it
    out of the stats index instead of poisoning every price-range query.
    """
    if value < min_value:
        return None
    if max_value is not None and value > max_value:
        return None
    return value


def parse_money_vn(
    text: str, *, min_value: int = 0, max_value: int | None = None,
) -> int | None:
    """Parse the FIRST money amount in *text* to integer VND per the standard.

    Handles every form in the module docstring: grouped thousands
    ("1.200.000", "700,000"), bare integers, decimals, VN/EN unit suffixes
    (k / tr / triệu / tỷ / M / nghìn / ngàn / đồng) and the "1tr499" compound.

    @param min_value: amounts below this are rejected (ingest passes the price
        floor so a row index / SKU never registers as a price; query passes 0).
    @param max_value: amounts above this are rejected (ingest passes the price
        ceiling so a date/timestamp/barcode never registers as a price; None =
        no ceiling, the query side keeps it open).
    @return integer VND, or None when no money token is present / out of band.
    """
    if not text or not text.strip():
        return None
    s = text.strip()

    # 1. "tr" compound — highest specificity, parse before bare digits.
    m = _TR_COMPOUND_RE.search(s)
    if m:
        base = _normalize_literal(m.group(1), suffix_context=True)
        if base is not None:
            extra = int(m.group(2)) * 1_000 if m.group(2) else 0
            return _guard(round(base * 1_000_000 + extra), min_value, max_value)

    # 2. number + unit suffix.
    m = _SUFFIX_RE.search(s)
    if m:
        base = _normalize_literal(m.group(1), suffix_context=True)
        mult = _SUFFIX_MULT.get(_fold(m.group(2)))
        if base is not None and mult is not None:
            return _guard(round(base * mult), min_value, max_value)

    # 3. bare / grouped number (no suffix).
    m = _NUMERIC_RE.search(s)
    if m:
        val = _normalize_literal(m.group(0), suffix_context=False)
        if val is not None:
            return _guard(round(val), min_value, max_value)

    return None


# A "significant" number worth coverage-checking: a digit-group (optionally
# grouped by '.'/',') . Bare 1-3 digit tokens (ordinals / STT 1,2,3 / sizes) are
# excluded by the caller's ``min_digits`` floor so an index is not flagged.
_SIGNIFICANT_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\d[\d.,]*\d|\d")


def find_dropped_numbers(
    source: str,
    chunks: list[str],
    *,
    min_digits: int = DEFAULT_NUMERIC_COVERAGE_MIN_DIGITS,
) -> list[str]:
    """Return source numeric tokens that appear in NO chunk — a silently-dropped
    value (the "honest but blind" HALLU class: a chunker that loses a price/row
    leaves Faithfulness at 1.0 while the number is simply gone).

    Lossless-coverage signal, OBSERVE-only: literal-substring match (chunking must
    never reformat a number, so a dropped token is a real gap), deterministic, no
    LLM, domain/currency/language-neutral. Tokens with fewer than ``min_digits``
    digits are ignored (ordinals / row indices / sizes are not values).

    @return deduped list of dropped numeric tokens (empty = full numeric coverage).
    """
    if not source or not chunks:
        return []
    joined = "\n".join(chunks)
    seen: set[str] = set()
    missing: list[str] = []
    for m in _SIGNIFICANT_NUMBER_RE.finditer(source):
        tok = m.group(0)
        if sum(c.isdigit() for c in tok) < min_digits:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        if tok not in joined:
            missing.append(tok)
    return missing


__all__ = ["find_dropped_numbers", "parse_money_vn"]
