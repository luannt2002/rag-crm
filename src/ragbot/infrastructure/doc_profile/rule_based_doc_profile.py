"""RuleBasedDocumentProfileAnalyzer — AdapChunk Layer 3 refine implementation.

Inspired by the internal AdapChunk Layer 3 blueprint + Ekimetrics LREC
2026 proven adaptive-chunking metrics. Pure rule-based extraction — no
LLM call, no external lang-detect dependency — so the analyzer stays
cheap and deterministic in the ingest hot path.

The implementation populates every field on ``DocumentProfile``:

* heading_counts (h1, h2, h3, h4)        — markdown header detection
* has_toc                                — heuristic TOC scan (first 30 lines)
* table_count + table_avg_rows           — pipe/CSV table block detection
* formula_count                          — ``$...$`` / ``$$...$$`` regex
* image_count                            — ``![alt](url)`` regex
* code_block_count                       — ```` ``` ```` fence pairs
* avg_text_block_length                  — words/text-block ratio
* heading_ratio                          — headings / total_blocks
* mixed_content_score                    — table+code share of all blocks
* detected_language                      — VN-diacritic ratio classifier
* total_blocks                           — headings + tables + text blocks
* total_words                            — sum of all text-block word counts

The analyzer never raises on edge cases (empty, all-whitespace, binary
garbage) — every field has a safe default and the entity is always
well-formed so the ingest pipeline degrades gracefully.

Citation policy: AdapChunk concept is "inspired by internal blueprint"
(PhD private); the 10-feature breakdown follows the LREC 2026 Ekimetrics
proven set. We do NOT claim AdapChunk itself is peer-reviewed.
"""

from __future__ import annotations

import re
from typing import Final

import structlog

from ragbot.domain.entities.document_profile import DocumentProfile, HeadingCounts
from ragbot.shared.chunking import _is_table_line
from ragbot.shared.constants import (
    DEFAULT_CODE_FENCE_MARKER,
    DEFAULT_DOC_PROFILE_TOC_SCAN_LINES,
    DEFAULT_FORMULA_INLINE_RE,
    DEFAULT_IMAGE_MD_RE,
    DEFAULT_LANG_DETECT_FALLBACK,
    DEFAULT_LANG_DETECT_MIN_ALPHA_CHARS,
    DEFAULT_VN_DIACRITIC_RATIO,
    VN_DIACRITIC_CHARS,
)

logger = structlog.get_logger(__name__)

_FORMULA_RE: Final[re.Pattern[str]] = re.compile(DEFAULT_FORMULA_INLINE_RE)
_IMAGE_RE: Final[re.Pattern[str]] = re.compile(DEFAULT_IMAGE_MD_RE)
_VN_DIACRITIC_SET: Final[frozenset[str]] = frozenset(VN_DIACRITIC_CHARS)
_TOC_MARKERS_LOWER: Final[tuple[str, ...]] = ("mục lục", "table of contents")
_LANG_VI: Final[str] = "vi"


def _detect_language(text: str) -> str:
    """Detect ``"vi"`` vs fallback by Vietnamese diacritic ratio.

    Returns ``DEFAULT_LANG_DETECT_FALLBACK`` ("auto") when the document
    has fewer than ``DEFAULT_LANG_DETECT_MIN_ALPHA_CHARS`` alphabetic
    characters — short input is unreliable for any language detector.

    Otherwise compute (vn_diacritic_chars / alpha_chars) and classify
    "vi" when the ratio crosses ``DEFAULT_VN_DIACRITIC_RATIO``. This
    avoids pulling in ``langdetect`` for a single boolean classifier
    while remaining accurate for the platform's primary language pair
    (Vietnamese vs English-or-anything-else).
    """
    if not text:
        return DEFAULT_LANG_DETECT_FALLBACK
    alpha_chars = 0
    vn_chars = 0
    for ch in text.lower():
        if ch.isalpha():
            alpha_chars += 1
            if ch in _VN_DIACRITIC_SET:
                vn_chars += 1
    if alpha_chars < DEFAULT_LANG_DETECT_MIN_ALPHA_CHARS:
        return DEFAULT_LANG_DETECT_FALLBACK
    ratio = vn_chars / alpha_chars
    return _LANG_VI if ratio >= DEFAULT_VN_DIACRITIC_RATIO else DEFAULT_LANG_DETECT_FALLBACK


def _count_code_blocks(lines: list[str]) -> int:
    """Count opening Markdown code-fence markers.

    Each ``` line that toggles the "inside fence" state from OFF→ON
    counts as one block; the closing fence does not double-count. Pure
    structural — agnostic to the language tag on the opening fence.
    """
    count = 0
    inside = False
    for ln in lines:
        if ln.strip().startswith(DEFAULT_CODE_FENCE_MARKER):
            if not inside:
                count += 1
            inside = not inside
    return count


class RuleBasedDocumentProfileAnalyzer:
    """Populate ``DocumentProfile`` via rule-based heuristics — no LLM.

    The analyzer is stateless and side-effect free; the same input text
    always yields the same profile. Configuration knobs (regex patterns,
    diacritic threshold, TOC scan window) come from ``shared/constants``
    — no magic numbers inline. Adding a new feature = add a constant +
    a counter inside ``analyze`` (Open-Closed at the field level).
    """

    @staticmethod
    def get_provider_name() -> str:
        return "rule_based"

    def analyze(self, text: str) -> DocumentProfile:
        """Compute the 10-feature profile for ``text``.

        Edge cases:
        * ``None`` / empty / whitespace-only → zero-valued profile with
          ``detected_language = DEFAULT_LANG_DETECT_FALLBACK``.
        * Documents without any structural markup still yield a profile
          (everything counted under text_blocks).
        """
        if not text or not text.strip():
            return DocumentProfile(
                heading_counts=HeadingCounts(),
                has_toc=False,
                table_count=0,
                table_avg_rows=0.0,
                formula_count=0,
                image_count=0,
                code_block_count=0,
                avg_text_block_length=0.0,
                heading_ratio=0.0,
                mixed_content_score=0.0,
                detected_language=DEFAULT_LANG_DETECT_FALLBACK,
                total_blocks=0,
                total_words=0,
            )

        lines = text.split("\n")

        h1 = h2 = h3 = h4 = 0
        table_count = 0
        text_blocks = 0
        total_words = 0
        table_row_total = 0
        table_row_current = 0
        in_table = False
        in_code_fence = False

        for raw_line in lines:
            stripped = raw_line.strip()

            # Code-fence handling — content inside fences is NOT counted
            # as headings/tables/text (preserves atomic-block semantics
            # the downstream chunker also relies on).
            if stripped.startswith(DEFAULT_CODE_FENCE_MARKER):
                in_code_fence = not in_code_fence
                # Closing a table run if we hit a fence
                if in_table:
                    table_row_total += table_row_current
                    table_row_current = 0
                    in_table = False
                continue
            if in_code_fence:
                continue

            if stripped.startswith("#### "):
                h4 += 1
                if in_table:
                    table_row_total += table_row_current
                    table_row_current = 0
                    in_table = False
            elif stripped.startswith("### "):
                h3 += 1
                if in_table:
                    table_row_total += table_row_current
                    table_row_current = 0
                    in_table = False
            elif stripped.startswith("## "):
                h2 += 1
                if in_table:
                    table_row_total += table_row_current
                    table_row_current = 0
                    in_table = False
            elif stripped.startswith("# "):
                h1 += 1
                if in_table:
                    table_row_total += table_row_current
                    table_row_current = 0
                    in_table = False
            elif _is_table_line(stripped):
                if not in_table:
                    table_count += 1
                    in_table = True
                    table_row_current = 1
                else:
                    table_row_current += 1
            else:
                if in_table:
                    table_row_total += table_row_current
                    table_row_current = 0
                    in_table = False
                if stripped:
                    text_blocks += 1
                    total_words += len(stripped.split())

        # Flush trailing table row buffer (document ended mid-table).
        if in_table:
            table_row_total += table_row_current

        headings = HeadingCounts(h1=h1, h2=h2, h3=h3, h4=h4)
        total_headings = headings.total
        total_blocks = total_headings + table_count + text_blocks

        avg_text_block_length = total_words / text_blocks if text_blocks > 0 else 0.0
        heading_ratio = total_headings / total_blocks if total_blocks > 0 else 0.0
        table_avg_rows = table_row_total / table_count if table_count > 0 else 0.0

        formula_count = len(_FORMULA_RE.findall(text))
        image_count = len(_IMAGE_RE.findall(text))
        code_block_count = _count_code_blocks(lines)

        # Mixed-content score: share of "atomic-style" blocks (tables +
        # code) over the total block count. Anchors AdapChunk Layer 3
        # strategy selection when downstream code reads the entity.
        mixed_content_score = (
            (table_count + code_block_count) / total_blocks if total_blocks > 0 else 0.0
        )

        # TOC scan — top N lines only (most TOCs sit at the head).
        head = lines[:DEFAULT_DOC_PROFILE_TOC_SCAN_LINES]
        has_toc = any(
            any(marker in ln.lower() for marker in _TOC_MARKERS_LOWER) for ln in head
        )

        detected_language = _detect_language(text)

        return DocumentProfile(
            heading_counts=headings,
            has_toc=has_toc,
            table_count=table_count,
            table_avg_rows=round(table_avg_rows, 4),
            formula_count=formula_count,
            image_count=image_count,
            code_block_count=code_block_count,
            avg_text_block_length=round(avg_text_block_length, 4),
            heading_ratio=round(heading_ratio, 4),
            mixed_content_score=round(mixed_content_score, 4),
            detected_language=detected_language,
            total_blocks=total_blocks,
            total_words=total_words,
        )


__all__ = ["RuleBasedDocumentProfileAnalyzer"]
