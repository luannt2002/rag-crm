"""Structured-reference extraction for Vietnamese legal / regulatory corpora.

Scans a chunk of text for explicit structural anchors (Điều / Chương / Khoản
/ Mục / Phụ lục — Latin + Roman numerals) and returns a metadata dict that
ingest persists onto ``document_chunks.metadata_json``. The retrieval path
uses these fields to pre-filter chunks when the user's query contains the
same structured reference (e.g. "Điều 3?" → only consider chunks whose
``article_no == "3"``), which sidesteps dense-encoder weakness on short
keyword + number queries.

Domain-neutral by design: the regex matches a literal Latin-script keyword
("Điều", "Chương", etc.) — Vietnamese legal corpora are the immediate
beneficiary, but any document using the same convention (e.g. an English
translation that preserves the marker) gets the same lift. Bot owners with
no structured corpora simply see all keys absent on every chunk — zero cost,
zero behaviour change.

Function is pure + sync — safe to call from the ingest hot path inside the
parent ``DocumentService.ingest`` await chain.
"""

from __future__ import annotations

import re
from typing import Final

# Compiled at module load so the ingest loop doesn't re-compile per chunk.
# ``(?i)`` case-insensitive so "Điều" / "ĐIỀU" / "điều" all match. ``\b``
# avoids matching "Phụ liệu" prefixes. Number group captures the digit run.
_ARTICLE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bĐiều\s+(\d{1,4})\b", re.IGNORECASE
)
_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bKhoản\s+(\d{1,4})\b", re.IGNORECASE
)
_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"\bMục\s+(\d{1,4})\b", re.IGNORECASE
)
_APPENDIX_RE: Final[re.Pattern[str]] = re.compile(
    r"\bPhụ\s+lục\s+([A-Z0-9]{1,4})\b", re.IGNORECASE
)
# Roman numerals (I, II, III, IV, V, ..., XX) plus Latin digits (fallback).
_CHAPTER_RE: Final[re.Pattern[str]] = re.compile(
    r"\bChương\s+([IVXLCDM]{1,6}|\d{1,4})\b", re.IGNORECASE
)


def extract_structured_refs(text: str) -> dict[str, str]:
    """Return a dict of structured-reference metadata for ``text``.

    Keys present only when the regex matches. The *first* occurrence in the
    chunk wins — chunks that span multiple articles store the leading one,
    which matches user expectation ("the chunk starting with Điều 3").

    @param text: chunk content (post-enrichment is fine — the prefix won't
        contain literal "Điều N" unless the source already did).
    @return: dict with optional keys ``article_no``, ``clause_no``,
        ``section_no``, ``appendix_no``, ``chapter_no``. All values strings
        (regex group output) so JSONB persistence is straightforward.
    """
    if not text:
        return {}
    out: dict[str, str] = {}
    m = _ARTICLE_RE.search(text)
    if m:
        out["article_no"] = m.group(1)
    m = _CLAUSE_RE.search(text)
    if m:
        out["clause_no"] = m.group(1)
    m = _SECTION_RE.search(text)
    if m:
        out["section_no"] = m.group(1)
    m = _APPENDIX_RE.search(text)
    if m:
        out["appendix_no"] = m.group(1).upper()
    m = _CHAPTER_RE.search(text)
    if m:
        out["chapter_no"] = m.group(1).upper()
    return out


def extract_structured_ref_from_query(query: str) -> dict[str, str]:
    """Return structured-ref keys the user's *query* asks about.

    Same regex bank as :func:`extract_structured_refs` but applied to the
    incoming question. Retrieval can call this to decide whether to apply a
    metadata pre-filter. Empty dict → user did not reference a structured
    anchor → don't filter (full hybrid search runs unmodified).
    """
    return extract_structured_refs(query)


__all__ = [
    "extract_structured_refs",
    "extract_structured_ref_from_query",
]
