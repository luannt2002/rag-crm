"""Vietnamese legal-structure helpers for chunking (Chương/Mục/Điều).

Pure string utilities the chunker uses to detect + canonicalise Vietnamese
legal hierarchy markers (roman→arabic numerals, heading promotion, structural
anchors for retrieval pre-filter). No I/O, no state — extracted from the
chunking god-file so each concern lives in its own ≤1.2k module. Re-exported
by ``chunking/__init__`` so every existing import path stays unchanged.
"""
from __future__ import annotations

import re

from ragbot.shared.constants import (
    DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES,
    DEFAULT_STRUCTURAL_MARKERS_BY_LANG,
    DEFAULT_STRUCTURAL_MARKERS_LANG,
)



# ---------------------------------------------------------------------------
# Document profiling + strategy selection
# ---------------------------------------------------------------------------


# Hierarchical text promotion (VN admin/legal documents)
# ---------------------------------------------------------------------------
# Vietnamese administrative/legal documents (Thông tư, Nghị định, Quyết định,
# Luật...) carry hierarchy as plain text: "Chương III" / "Mục 2" / "Điều 13"
# rather than markdown "# Chương III". Without promotion, the HDT detector
# counts zero headings and falls back to flat recursive chunking, losing the
# Chapter > Section > Article path that gives each Article its citation
# context. The pattern is generic to VN legal/admin prose (not customer- or
# brand-specific), so we apply it at the platform level.
#
# Triggered only when ≥ DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES section
# markers appear at line start, so a single "Điều 1 nên..." mention in a
# casual FAQ is not promoted into a fake H3.
#
# Levels: Chương → H1, Mục → H2, Điều → H3. Khoản (1./2./3.) stay inline
# inside the Article content to keep one Article = one atomic unit and avoid
# 3-5× chunk-count explosion.
#
# Multi-language hardening: the marker prefixes ("Chương" / "Phần" / "Mục" /
# "Điều") are no longer inlined here — they come from
# ``DEFAULT_STRUCTURAL_MARKERS_BY_LANG`` keyed by language code, so a non-VN
# bot resolves its own set (empty / EN) instead of the VN literals. The
# regexes are rebuilt from the ordered marker tuple; the default-language
# (``vi``) tuple is byte-identical to the prior hardcoded values, so VN
# detection/promotion stays unchanged. The level grouping below (Chapter+Part
# share H1, Section→H2, Article→H3) is the structural convention shared across
# legal document languages, so it is keyed by tuple position, not by literal.

# Default-language marker tuple, ordered: [chapter, part, section, article].
_STRUCT_MARKERS: tuple[str, ...] = DEFAULT_STRUCTURAL_MARKERS_BY_LANG[
    DEFAULT_STRUCTURAL_MARKERS_LANG
]


def _alt(*markers: str) -> str:
    """Build a regex alternation ``(a|b|c)`` from ordered marker literals."""
    return "(" + "|".join(markers) + ")"


# Tuple-position aliases keep the level grouping readable + byte-identical to
# the prior literal-based regexes for the default (vi) language.
_M_CHAPTER, _M_PART, _M_SECTION, _M_ARTICLE = (
    _STRUCT_MARKERS[0],
    _STRUCT_MARKERS[1],
    _STRUCT_MARKERS[2],
    _STRUCT_MARKERS[3],
)

_VN_CHAPTER_RE = re.compile(
    r"^" + _alt(_M_CHAPTER, _M_PART) + r"\s+([IVXLCDM]+|[0-9]+)(\s*[\.:\-].*)?$",
    re.IGNORECASE,
)
_VN_SECTION_RE = re.compile(
    r"^" + re.escape(_M_SECTION) + r"\s+([IVXLCDM]+|[0-9]+)(\s*[\.:\-].*)?$",
    re.IGNORECASE,
)
_VN_ARTICLE_RE = re.compile(
    r"^" + re.escape(_M_ARTICLE) + r"\s+([0-9]+)\s*[\.:].*$",
    re.IGNORECASE,
)
_VN_HEADING_DETECT_RE = re.compile(
    r"^" + _alt(_M_CHAPTER, _M_PART, _M_SECTION, _M_ARTICLE) + r"\s+([IVXLCDM0-9]+)",
    re.IGNORECASE,
)


_ROMAN_VALUES: dict[str, int] = {
    "I": 1, "V": 5, "X": 10, "L": 50,
    "C": 100, "D": 500, "M": 1000,
}
_ROMAN_FULLMATCH_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)


def _arabic_to_roman(n: int) -> str:
    """Convert positive int 1..3999 → Roman numeral string. Empty on invalid."""
    if n <= 0 or n >= 4000:
        return ""
    pairs = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out: list[str] = []
    for val, sym in pairs:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


def roman_to_arabic(s: str) -> int | None:
    """Convert Roman numeral string → Arabic int. Return None if invalid.

    Validated by round-trip (catches malformed e.g. ``IIII``, ``VV``).
    Case-insensitive. Empty / non-roman / arabic input returns ``None``.

    Examples:
        roman_to_arabic("III") == 3
        roman_to_arabic("IV") == 4
        roman_to_arabic("XIII") == 13
        roman_to_arabic("MCMXCIV") == 1994
        roman_to_arabic("IIII") is None  # malformed
        roman_to_arabic("3") is None     # not roman
        roman_to_arabic("") is None
    """
    s_clean = s.upper().strip()
    if not s_clean or not _ROMAN_FULLMATCH_RE.fullmatch(s_clean):
        return None
    total, prev = 0, 0
    for ch in reversed(s_clean):
        val = _ROMAN_VALUES[ch]
        total += val if val >= prev else -val
        prev = val
    if _arabic_to_roman(total) != s_clean:
        return None
    return total


# Section-level normalisation covers Chapter/Part/Section markers (NOT the
# Article level — Article numerals are always Arabic in the TT format, so they
# need no roman→arabic pass). Built from the first three ordered markers.
_VN_SECTION_NORMALIZE_RE = re.compile(
    r"\b" + _alt(_M_CHAPTER, _M_PART, _M_SECTION) + r"\s+([IVXLCDM]+|[0-9]+)\b",
    re.IGNORECASE,
)

# Lowercase → canonical Title-case map for every marker in the language set.
# 2026-05-27 — the Article-level prefix is included so
# ``detect_vn_structural_anchor`` (legal-corpus structural pre-filter) can
# canonicalise lowercase / uppercase inputs to the form chunk paths store.
_VN_PREFIX_CANONICAL: dict[str, str] = {
    marker.lower(): marker for marker in _STRUCT_MARKERS
}


def normalize_vn_section_numerals(text: str) -> str:
    """Normalize Vietnamese legal section markers to canonical form.

    Both case AND numeral are canonicalised: prefix → Title-case
    ('chương' / 'CHƯƠNG' → 'Chương'), numeral → Arabic ('III' → '3').
    Required because embedding models (zembed-1) and BM25 treat
    'chương 3' (lowercase) and 'Chương 3' (Title) as different tokens,
    so a single canonical form ensures both ingest-side chunk paths and
    query-side rewrites produce vectors that align.

    Examples:
        normalize_vn_section_numerals("Chương III") == "Chương 3"
        normalize_vn_section_numerals("chương 3") == "Chương 3"  # case fixup
        normalize_vn_section_numerals("CHƯƠNG iii") == "Chương 3"
        normalize_vn_section_numerals("Mục V > Điều 55") == "Mục 5 > Điều 55"
        normalize_vn_section_numerals("[Chương III > Mục 2]") == "[Chương 3 > Mục 2]"
        normalize_vn_section_numerals("Chương 3") == "Chương 3"  # idempotent
        normalize_vn_section_numerals("Vào lúc 5 giờ") == "Vào lúc 5 giờ"
    """
    def _repl(m: "re.Match[str]") -> str:
        prefix_raw = m.group(1)
        numeral = m.group(2)
        prefix_canonical = _VN_PREFIX_CANONICAL.get(
            prefix_raw.lower(), prefix_raw,
        )
        # Numeral: if roman → arabic; if already arabic → keep as-is.
        arabic = roman_to_arabic(numeral)
        if arabic is None:
            # Already arabic (or invalid roman). Keep numeral as-is but
            # still Title-case the prefix.
            return f"{prefix_canonical} {numeral}"
        return f"{prefix_canonical} {arabic}"
    return _VN_SECTION_NORMALIZE_RE.sub(_repl, text)


# 2026-05-27 — structural identifier detector for VN legal corpora.
# When query contains a Chương/Mục/Phần/Điều + number, retrieve can
# pre-filter pgvector to chunks whose content path matches that
# identifier — bypasses embedding model's weak grasp of structural
# anchors. Use canonical Arabic form (normalize_vn_section_numerals
# already applied upstream in condense_question).
_VN_STRUCTURAL_QUERY_DETECT_RE = re.compile(
    r"\b" + _alt(_M_CHAPTER, _M_PART, _M_SECTION, _M_ARTICLE)
    + r"\s+([0-9]+|[IVXLCDM]+)\b",
    re.IGNORECASE,
)


def detect_vn_structural_anchor(query: str) -> tuple[str, str] | None:
    """Return (prefix, arabic_numeral) when query has a VN legal anchor.

    Examples:
        detect_vn_structural_anchor('Chương 3 nói gì') == ('Chương', '3')
        detect_vn_structural_anchor('Điều 55') == ('Điều', '55')
        detect_vn_structural_anchor('Mục III và Điều 22') is None  # multi-anchor
        detect_vn_structural_anchor('giá triệt lông') is None

    Multi-anchor queries return None (orchestrator should NOT pre-filter —
    too restrictive; let normal retrieve handle).
    """
    if not query or not isinstance(query, str):
        return None
    matches = list(_VN_STRUCTURAL_QUERY_DETECT_RE.finditer(query))
    if len(matches) != 1:
        return None
    m = matches[0]
    prefix_raw = m.group(1)
    numeral = m.group(2)
    arabic = roman_to_arabic(numeral)
    if arabic is None:
        arabic_str = numeral  # already arabic
    else:
        arabic_str = str(arabic)
    # Normalize prefix Title-case
    prefix_canonical = _VN_PREFIX_CANONICAL.get(
        prefix_raw.lower(), prefix_raw,
    )
    return (prefix_canonical, arabic_str)


def build_vn_structural_like_clauses(anchor: tuple[str, str]) -> list[str]:
    """Return list of LIKE patterns that match chunks under the anchor.

    Chunks store path like '[Chương 3 > Điều 55]' or 'Chương 3, Mục 1, Điều 55'.
    The 4 patterns cover the dominant formats observed in the corpus.
    """
    prefix, num = anchor
    return [
        f"%[{prefix} {num}%",      # bracketed path: [Chương 3 > ...]
        f"%{prefix} {num},%",       # comma-separated: Chương 3, Mục 1, Điều 55
        f"%{prefix} {num} >%",      # arrow path inline: Chương 3 > Điều 55
        f"%{prefix} {num}]%",       # closing bracket: [Chương 3] heading-only
    ]


def promote_vn_hierarchical_headings(text: str) -> str:
    """Convert plain-text "Chương/Mục/Điều" markers into markdown ATX headings.

    Only fires when ≥ ``DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES`` markers
    appear at line start. Returns the original text unchanged otherwise so
    that non-legal documents are not touched.
    """
    lines = text.split("\n")

    match_count = sum(
        1 for line in lines if _VN_HEADING_DETECT_RE.match(line.strip())
    )
    if match_count < DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES:
        return text

    promoted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            promoted.append(line)
            continue
        if _VN_CHAPTER_RE.match(stripped):
            # Normalize Roman → Arabic so chunk paths store one canonical
            # form. Query rewriter applies the same normalization so 'chương
            # 3' and 'Chương III' both match the stored 'Chương 3' path.
            stripped = normalize_vn_section_numerals(stripped)
            promoted.append(f"# {stripped}")
        elif _VN_SECTION_RE.match(stripped):
            stripped = normalize_vn_section_numerals(stripped)
            promoted.append(f"## {stripped}")
        elif _VN_ARTICLE_RE.match(stripped):
            # Điều is always Arabic in TT format — no normalization needed
            # but call is idempotent so safe to keep symmetric.
            stripped = normalize_vn_section_numerals(stripped)
            promoted.append(f"### {stripped}")
        else:
            promoted.append(line)

    return "\n".join(promoted)


__all__ = [
    "roman_to_arabic",
    "normalize_vn_section_numerals",
    "detect_vn_structural_anchor",
    "build_vn_structural_like_clauses",
    "promote_vn_hierarchical_headings",
    "_VN_CHAPTER_RE",
    "_VN_SECTION_RE",
    "_VN_ARTICLE_RE",
    "_VN_HEADING_DETECT_RE",
    "_VN_SECTION_NORMALIZE_RE",
    "_VN_STRUCTURAL_QUERY_DETECT_RE",
    "_VN_PREFIX_CANONICAL",
    "_ROMAN_VALUES",
    "_ROMAN_FULLMATCH_RE",
    "_arabic_to_roman",
]
