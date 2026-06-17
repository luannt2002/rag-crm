"""Math-lockdown — regex + Decimal normalization to verify every numeric
claim in a bot answer appears verbatim (after normalization) in the retrieved
chunks.

Pure module: no I/O, no config, no logging. Query-graph integrates it.

Covered claim types:
- Money in VND:
    "1.199.000đ", "1,199,000 VND", "1199000 đồng",
    "199k", "60 nghìn", "1.2 triệu", "2 tr", "1tr2" (→ 1,200,000)
- Percent:
    "50%"
- Duration:
    "30 phút", "1 giờ", "1 tiếng", "10 buổi", "5 lần",
    "7 ngày", "2 tuần", "3 tháng", "1 năm"

NOT covered (intentional — too many false positives):
- Bare years ("2026")
- Bare IDs ("12345")
- Phone numbers (handled at a higher layer if needed)

All functions are pure. Input strings are NFC-agnostic — callers normalize
upstream if they care about Unicode homoglyphs.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

# --- Money — Vietnamese conventions -----------------------------------------
# VND normalized to integer; sub-đồng fractions don't exist in practice.

# Suffix order = longest-first so "triệu" wins over "tr".
_VND_SUFFIX = (
    r"(?P<unit>đồng|vnd|vnđ|đ|triệu|trieu|nghìn|nghin|tỷ|ty|tr|k)"
)
_NUM_TOKEN = r"(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"

# Trailing non-letter required so "30k" doesn't swallow the "k" of "kg".
_MONEY_RE = re.compile(
    rf"{_NUM_TOKEN}\s*{_VND_SUFFIX}(?![a-zA-ZÀ-ỹ])",
    re.IGNORECASE | re.UNICODE,
)

# Bare Vietnamese dotted amount (≥2 thousand-groups, i.e. ≥1.000.000) with NO
# unit suffix — e.g. "3.000.000 đến 6.000.000". A model overrides such legal
# penalty values from parametric memory (wrote 3.000.000 where the doc says
# 4.000.000); because the unit "đồng" trails only the LAST number in a range,
# the bare leading amount escaped _MONEY_RE. The 3-digit groups exclude dates
# (01.01.2022 → groups "01" are 2-digit) and the boundary guards avoid eating a
# longer number. Treated as VND (dots stripped → integer).
_BIG_VND_RE = re.compile(r"(?<![\d.])(?P<num>\d{1,3}(?:\.\d{3}){2,})(?![\d.])")

_PERCENT_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*%",
    re.UNICODE,
)

_DURATION_UNITS = (
    "phút",
    "giờ",
    "tiếng",
    "buổi",
    "lần",
    "ngày",
    "tuần",
    "tháng",
    "năm",
)
_DURATION_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>"
    + "|".join(_DURATION_UNITS)
    + r")(?![a-zA-ZÀ-ỹ])",
    re.IGNORECASE | re.UNICODE,
)

# Document/citation number "NN/YYYY" (e.g. 18/2018, 100/2019) — the form used
# for Thông tư / Nghị định / Quyết định references. A small answer model
# confidently fabricates these from parametric memory (e.g. "thay thế 16/2018"
# when the corpus says 18/2018), so they are exactly the tokens that must be
# grounded in a retrieved chunk. The leading guard ``(?<![\d/])`` and trailing
# ``(?!/?\d)`` exclude the YYYY tail of a dd/mm/yyyy date so real dates are not
# mis-read as citation numbers (dates vary in format and are left out to avoid
# false positives). Normalised by stripping leading zeros per component.
_DOCNUM_RE = re.compile(r"(?<![\d/])(?P<num>\d{1,3}/\d{4})(?!/?\d)")


def _to_decimal_vnd_multiplier(num_str: str, unit: str) -> Decimal | None:
    """Parse '1.2', '1,2', '1199000' etc. into a Decimal. None on failure.

    Heuristic: if both separators present, the last is decimal; the other is
    thousand. Solo separator splitting into 3-digit groups = thousand; else decimal.
    """
    s = num_str
    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_dot > last_comma:
            s = s.replace(",", "")
        else:
            s = s.replace(".", "")
            s = s.replace(",", ".")
    elif has_dot:
        parts = s.split(".")
        if len(parts) > 1 and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
            s = s.replace(".", "")
    elif has_comma:
        parts = s.split(",")
        if len(parts) > 1 and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")

    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _money_to_vnd_int(num_str: str, unit: str) -> str | None:
    """Apply VND unit multiplier and return integer-VND string."""
    d = _to_decimal_vnd_multiplier(num_str, unit)
    if d is None:
        return None

    u = unit.lower()
    if u in ("đ", "đồng", "vnd", "vnđ"):
        multiplier = Decimal(1)
    elif u in ("k", "nghìn", "nghin"):
        multiplier = Decimal(1_000)
    elif u in ("triệu", "trieu"):
        multiplier = Decimal(1_000_000)
    elif u in ("tr",):
        multiplier = Decimal(1_000_000)
    elif u in ("tỷ", "ty"):
        multiplier = Decimal(1_000_000_000)
    else:
        return None

    total = d * multiplier
    # Round down to integer VND (no fractional đồng in practice).
    return str(int(total))


def _percent_to_norm(num_str: str) -> str | None:
    """Return percent as a string — '50' or '12.5'. Keep int form when
    possible so "50%" and "50.0%" collapse to the same token."""
    d = _to_decimal_vnd_multiplier(num_str, "")
    if d is None:
        return None
    if d == d.to_integral_value():
        return str(int(d))
    # Normalize trailing zero like "12.50" → "12.5"
    return str(d.normalize())


def _duration_to_norm(num_str: str) -> str | None:
    """Same idea as percent: integer when possible, else decimal."""
    return _percent_to_norm(num_str)


def extract_numeric_claims(text: str) -> set[tuple[str, str]]:
    """Return set of (normalized_number_str, unit) found in text.

    Units used:
        "VND"    — all money forms normalize to integer VND.
        "%"      — percent.
        "phút" | "giờ" | "tiếng" | "buổi" | "lần"
            | "ngày" | "tuần" | "tháng" | "năm"
            — duration units, kept in their Vietnamese canonical lowercase.
    """
    if not text:
        return set()

    claims: set[tuple[str, str]] = set()

    for m in _MONEY_RE.finditer(text):
        norm = _money_to_vnd_int(m.group("num"), m.group("unit"))
        if norm is not None:
            claims.add((norm, "VND"))

    for m in _BIG_VND_RE.finditer(text):
        # Bare dotted amount → strip separators → integer VND.
        claims.add((m.group("num").replace(".", ""), "VND"))

    for m in _PERCENT_RE.finditer(text):
        norm = _percent_to_norm(m.group("num"))
        if norm is not None:
            claims.add((norm, "%"))

    for m in _DURATION_RE.finditer(text):
        norm = _duration_to_norm(m.group("num"))
        if norm is not None:
            claims.add((norm, m.group("unit").lower()))

    for m in _DOCNUM_RE.finditer(text):
        # Normalise "01/2018" → "1/2018" so format variance doesn't cause a
        # spurious ungrounded flag; the value is an identifier, not a quantity.
        parts = m.group("num").split("/")
        norm = "/".join(str(int(p)) for p in parts)
        claims.add((norm, "docref"))

    return claims


def find_ungrounded_numbers(
    answer: str,
    chunk_texts: Iterable[str],
) -> list[dict[str, str]]:
    """Return list of {'value': normalized, 'unit': unit} for numbers in the
    answer that do NOT appear (after normalization) in any chunk.

    Empty answer → []. Empty chunks → all answer claims flagged.
    """
    if not answer:
        return []

    answer_claims = extract_numeric_claims(answer)
    if not answer_claims:
        return []

    chunk_claims: set[tuple[str, str]] = set()
    for ct in chunk_texts:
        if ct:
            chunk_claims |= extract_numeric_claims(ct)

    ungrounded = answer_claims - chunk_claims
    return [{"value": v, "unit": u} for (v, u) in sorted(ungrounded)]


__all__ = [
    "extract_numeric_claims",
    "find_ungrounded_numbers",
]
