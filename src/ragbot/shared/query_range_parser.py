"""Parse Vietnamese range queries for stats-index routing.

Supports patterns like:
  "dưới 2tr"  → price_max=2_000_000
  "trên 500k" → price_min=500_000
  "từ 500k đến 2tr" → price_min=500_000, price_max=2_000_000
  "khoảng 1 triệu" → fuzzy ±10%

When the query also contains an aggregation signal ("có bao nhiêu", "liệt kê", ...)
the operation field is set accordingly so the caller can choose COUNT vs LIST SQL.

Returns None when no range pattern is detected — caller falls back to vector retrieve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ragbot.shared.constants import (
    RANGE_QUERY_MIN_CONFIDENCE,
    SUPERLATIVE_QUERY_CONFIDENCE,
    SUMMARY_QUERY_PATTERNS_VI,
)
from ragbot.shared.number_format import parse_money_vn as _canonical_parse_money

# ---------------------------------------------------------------------------
# Money normalisation — delegates to the canonical platform NUMBER STANDARD
# (shared.number_format) so the query side reads "1.200.000" / "700,000"
# identically to the ingest side. A second divergent parser here was the root
# cause of "dưới 700.000" parsing to 700 while the corpus stored 700000.
# ---------------------------------------------------------------------------

# Vietnamese diacritics normalisation helpers (ascii fold for pattern match)
_DIACRITIC_MAP: Final[dict[str, str]] = {
    "đ": "d", "ư": "u", "ơ": "o", "ă": "a", "â": "a",
    "ê": "e", "ô": "o",
    "ự": "u", "ử": "u", "ữ": "u", "ừ": "u", "ứ": "u",
    "ợ": "o", "ở": "o", "ỡ": "o", "ờ": "o", "ớ": "o",
    "ặ": "a", "ẳ": "a", "ẵ": "a", "ằ": "a", "ắ": "a",
    "ậ": "a", "ẩ": "a", "ẫ": "a", "ầ": "a", "ấ": "a",
    "ệ": "e", "ể": "e", "ễ": "e", "ề": "e", "ế": "e",
    "ộ": "o", "ổ": "o", "ỗ": "o", "ồ": "o", "ố": "o",
    "ị": "i", "ỉ": "i", "ĩ": "i", "ì": "i", "í": "i",
    "ụ": "u", "ủ": "u", "ũ": "u", "ù": "u", "ú": "u",
    "ỵ": "y", "ỷ": "y", "ỹ": "y", "ỳ": "y", "ý": "y",
    "ạ": "a", "ả": "a", "ã": "a", "à": "a", "á": "a",
    "ọ": "o", "ỏ": "o", "õ": "o", "ò": "o", "ó": "o",
    "ẹ": "e", "ẻ": "e", "ẽ": "e", "è": "e", "é": "e",
}

_TRANS_TABLE: Final[dict[int, str]] = {ord(k): v for k, v in _DIACRITIC_MAP.items()}


def _ascii_fold(text: str) -> str:
    """Strip Vietnamese diacritics for pattern matching."""
    return text.lower().translate(_TRANS_TABLE)


def parse_money_vn(text: str) -> int | None:
    """Normalise a Vietnamese money string to integer VND.

    Thin wrapper over the canonical ``shared.number_format.parse_money_vn`` so
    the query side and the ingest side share ONE number standard. Query passes
    ``min_value=0`` — small-number rejection is handled by the caller's
    document-number guard (``_find_money_after_token``), not the parser.

    Examples:
      "2tr"        → 2_000_000      "500k"       → 500_000
      "1.5 triệu"  → 1_500_000      "1.200.000"  → 1_200_000
      "700,000"    → 700_000        "300"        → 300
    """
    return _canonical_parse_money(text, min_value=0)


# ---------------------------------------------------------------------------
# Range filter dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RangeFilter:
    """Parsed range constraint extracted from a user query.

    price_min / price_max are VND integers; None = unbounded.
    price_column: "primary" | "secondary" | "any"
    operation:    "count" | "list" | "filter"
    confidence:   0–1 float; values below RANGE_QUERY_MIN_CONFIDENCE
                  should be ignored by the caller.
    """

    price_min: int | None
    price_max: int | None
    price_column: str
    operation: str
    confidence: float


# ---------------------------------------------------------------------------
# Operation-signal detection
# ---------------------------------------------------------------------------

_COUNT_SIGNALS: Final[tuple[str, ...]] = (
    "có bao nhiêu",
    "bao nhieu",
    "dem",
    "đếm",
    "so luong",
    "số lượng",
    "count",
)

_LIST_SIGNALS: Final[tuple[str, ...]] = (
    "liet ke",
    "liệt kê",
    "danh sach",
    "danh sách",
    "toan bo",
    "toàn bộ",
    "tat ca",
    "tất cả",
    "nhung gi",
    "những gì",
    "nhung cai",
    "những cái",
    "co nhung",
    "có những",
    "list",
)


def _detect_operation(folded: str) -> str:
    """Return "count" | "list" | "filter" based on presence of signals."""
    for sig in _COUNT_SIGNALS:
        if sig in folded:
            return "count"
    for sig in _LIST_SIGNALS:
        if sig in folded:
            return "list"
    return "filter"


# ---------------------------------------------------------------------------
# Range pattern matching
# ---------------------------------------------------------------------------

# Patterns in the original Unicode (used after stripping diacritics on a copy).
# We match on the diacritic-folded copy; money tokens are extracted from
# the original text so parse_money_vn works correctly on "2tr", "500k" etc.

# Tokens that indicate "below/max":
_BELOW_TOKENS: Final[tuple[str, ...]] = (
    "duoi",       # dưới
    "it hon",     # ít hơn
    "nho hon",    # nhỏ hơn
    "thap hon",   # thấp hơn
    "khong qua",  # không quá
    "toi da",     # tối đa
    "max",
    "< ",
    "<=",
)

# Tokens that indicate "above/min":
_ABOVE_TOKENS: Final[tuple[str, ...]] = (
    "tren",       # trên
    "hon",        # hơn (standalone "lớn hơn", "cao hơn")
    "lon hon",    # lớn hơn
    "cao hon",    # cao hơn
    "tu",         # từ (without "đến" — treated as lower bound only)
    "min",
    "> ",
    ">=",
)

# Price-superlative tokens (diacritic-folded). A superlative carries no numeric
# bound — it maps to an ORDER BY price DESC/ASC against the stats index. Only
# unambiguously price-ranking phrases are listed; "tốt nhất" (best) and
# duration/discount superlatives are intentionally excluded (handled, if at
# all, by SuperlativeContextEnricher on retrieved chunks). Domain-neutral.
_SUPERLATIVE_MAX_TOKENS: Final[tuple[str, ...]] = (
    "dat nhat",        # đắt nhất
    "mac nhat",        # mắc nhất
    "cao nhat",        # cao nhất (giá cao nhất)
    "cao cap nhat",    # cao cấp nhất
    "dat tien nhat",   # đắt tiền nhất
    "dat gia nhat",    # đắt giá nhất
    "most expensive",
    "highest price",
    "priciest",
    "dearest",
)
_SUPERLATIVE_MIN_TOKENS: Final[tuple[str, ...]] = (
    "re nhat",         # rẻ nhất
    "thap nhat",       # thấp nhất (giá thấp nhất)
    "re tien nhat",    # rẻ tiền nhất
    "re gia nhat",     # rẻ giá nhất
    "phai chang nhat", # phải chăng nhất
    "binh dan nhat",   # bình dân nhất
    "cheapest",
    "lowest price",
    "least expensive",
    "most affordable",
)

# Range pattern: "từ X đến Y" / "X - Y" / "khoảng X"
_RANGE_FROM_TO_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:tu\s+|from\s+)?(\d+(?:[.,]\d+)?\s*(?:ty|trieu|tr|nghin|ngan|k|dong)?)"
    r"\s*(?:den|to|-)\s*"
    r"(\d+(?:[.,]\d+)?\s*(?:ty|trieu|tr|nghin|ngan|k|dong)?)",
    re.IGNORECASE | re.UNICODE,
)

# Fuzzy "khoảng X" — extracts the centre point
_FUZZY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:khoang|khoảng|around|about)\s+"
    r"(\d+(?:[.,]\d+)?\s*(?:ty|trieu|tr|nghin|ngan|k|dong)?)",
    re.IGNORECASE | re.UNICODE,
)

# Fallback money extractor — grabs the first number+suffix in the query
_ANY_MONEY_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:[.,]\d+)?\s*(?:tỷ|triệu|tr|nghìn|ngàn|k(?!\w)|đồng)?)",
    re.IGNORECASE | re.UNICODE,
)

_FUZZY_MARGIN: Final[float] = 0.10  # ±10%
# Minimum value (VND) for a UNIT-LESS bare number to count as a price filter.
# A bare "9" / "18" / "56" is never a real price — it is almost always a
# document/article number ("Thông tư 09/2020", "Điều 18"). Real price filters
# carry a currency unit (k/tr/triệu/nghìn/đồng) or are far above this floor.
# Guards the ascii-fold collision where "Thông tư" folds to "...tu" and matches
# the range token "từ", grabbing the document number as a bogus price → which
# false-routed legal queries to the stats_index path (forensic 2026-06-05).
_MIN_BARE_PRICE_VND: Final[int] = 1000
_DATE_OR_DOCNUM_TAIL_RE: Final[re.Pattern[str]] = re.compile(r"\s*/\s*\d")


def parse_range_query(query: str) -> RangeFilter | None:
    """Detect a price-range pattern in a Vietnamese natural-language query.

    Returns a RangeFilter when a range pattern is detected with confidence
    >= RANGE_QUERY_MIN_CONFIDENCE, or None when the query shows no range
    signals (caller falls back to vector retrieve).

    Does not raise — all parse failures return None.
    """
    if not query or not query.strip():
        return None

    folded = _ascii_fold(query)
    operation = _detect_operation(folded)

    # --- 1. "từ X đến Y" / "X - Y" -------------------------------------------
    m_range = _RANGE_FROM_TO_RE.search(folded)
    if m_range:
        raw_lo = _extract_original_span(query, m_range.start(1), m_range.end(1))
        raw_hi = _extract_original_span(query, m_range.start(2), m_range.end(2))
        lo = parse_money_vn(raw_lo.strip())
        hi = parse_money_vn(raw_hi.strip())
        if lo is not None and hi is not None:
            price_min, price_max = (lo, hi) if lo <= hi else (hi, lo)
            return RangeFilter(
                price_min=price_min,
                price_max=price_max,
                price_column="any",
                operation=operation if operation != "filter" else "list",
                confidence=0.9,
            )

    # --- 2. "khoảng X" --------------------------------------------------------
    m_fuzzy = _FUZZY_RE.search(folded)
    if m_fuzzy:
        raw_val = _extract_original_span(query, m_fuzzy.start(1), m_fuzzy.end(1))
        centre = parse_money_vn(raw_val.strip())
        if centre is not None and centre > 0:
            margin = int(centre * _FUZZY_MARGIN)
            return RangeFilter(
                price_min=centre - margin,
                price_max=centre + margin,
                price_column="any",
                operation=operation if operation != "filter" else "list",
                confidence=0.75,
            )

    # --- 3. "dưới X" / "<X" ---------------------------------------------------
    for token in _BELOW_TOKENS:
        if token in folded:
            money = _find_money_after_token(query, folded, token)
            if money is not None and money > 0:
                return RangeFilter(
                    price_min=None,
                    price_max=money,
                    price_column="any",
                    operation=operation,
                    confidence=0.85,
                )

    # --- 4. "trên X" / ">X" ---------------------------------------------------
    for token in _ABOVE_TOKENS:
        if token in folded:
            money = _find_money_after_token(query, folded, token)
            if money is not None and money > 0:
                return RangeFilter(
                    price_min=money,
                    price_max=None,
                    price_column="any",
                    operation=operation,
                    confidence=0.85,
                )

    # --- 5. superlative "đắt nhất" / "rẻ nhất" (no numeric bound) -------------
    # Checked LAST: an explicit range bound above is more specific and wins.
    # Maps to ORDER BY price DESC (max) / ASC (min) on the stats index.
    for token in _SUPERLATIVE_MAX_TOKENS:
        if token in folded:
            return RangeFilter(
                price_min=None, price_max=None, price_column="any",
                operation="max", confidence=SUPERLATIVE_QUERY_CONFIDENCE,
            )
    for token in _SUPERLATIVE_MIN_TOKENS:
        if token in folded:
            return RangeFilter(
                price_min=None, price_max=None, price_column="any",
                operation="min", confidence=SUPERLATIVE_QUERY_CONFIDENCE,
            )

    return None


def _extract_original_span(original: str, start: int, end: int) -> str:
    """Return the substring of *original* aligned to folded-string offsets.

    The folded string produced by ``_ascii_fold`` is the same length as the
    original because we do a 1-to-1 character replacement (no combining
    marks are stripped, no characters are deleted). Slice is therefore safe.
    """
    return original[start:end]


def _find_money_after_token(original: str, folded: str, token: str) -> int | None:
    """Find the money amount that appears after *token* in the folded string."""
    idx = folded.find(token)
    if idx == -1:
        return None
    after = original[idx + len(token):]
    m = _ANY_MONEY_RE.search(after)
    if not m:
        return None
    span = m.group(1).strip()
    # Reject a number that is a date / document-number reference: a digit run
    # immediately followed by "/<digits>" ("09/2020", "18/2018") is a date or
    # doc id, not a price.
    if _DATE_OR_DOCNUM_TAIL_RE.match(after[m.end():]):
        return None
    money = parse_money_vn(span)
    if money is None:
        return None
    # A unit-less bare number below the sane floor is a doc/article number, not
    # a price (real price filters carry a unit or are well above the floor).
    _has_unit = bool(
        re.search(r"(tỷ|triệu|tr|nghìn|ngàn|k|đồng)", span, re.IGNORECASE)
    )
    if not _has_unit and money < _MIN_BARE_PRICE_VND:
        return None
    return money


# ---------------------------------------------------------------------------
# Summary-pattern matching
# ---------------------------------------------------------------------------


def matches_summary_pattern(query: str) -> bool:
    """Return True when the query asks for a doc-level summary / overview.

    Checks the query (case-insensitive, diacritic-folded) against the
    canonical SUMMARY_QUERY_PATTERNS_VI tuple from constants.
    """
    if not query:
        return False
    folded = _ascii_fold(query)
    for pattern in SUMMARY_QUERY_PATTERNS_VI:
        if _ascii_fold(pattern) in folded:
            return True
    return False


__all__ = [
    "RangeFilter",
    "parse_money_vn",
    "parse_range_query",
    "matches_summary_pattern",
]
