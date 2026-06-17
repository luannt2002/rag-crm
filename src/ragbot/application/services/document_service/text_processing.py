"""Document text cleaning + chunk-type classification (pure, deterministic).

Extracted from the document_service god-file: VN-aware text normalisation
(hyphenation fix, prompt-injection strip, embed-text canonicalisation) and the
block-type → chunk-type mapping. No I/O, no DB — re-exported by
document_service/__init__ so existing imports (e.g. ``_fix_hyphenation``,
``should_skip_row_enrich``) stay unchanged.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Final  # noqa: F401

import structlog

from ragbot.shared.constants import (
    CR_ROW_GATED_STRATEGIES,
    DEFAULT_CHUNK_TYPE_CODE,
    DEFAULT_CHUNK_TYPE_TABLE,
    DEFAULT_CHUNK_TYPE_TABLE_ROW,
    DEFAULT_CHUNK_TYPE_TEXT,
    PROMPT_INJECTION_PATTERNS,
)
from ragbot.shared.text_normalization import normalize_vn

logger = structlog.get_logger(__name__)




_VIETNAMESE_DIACRITICS_RE = re.compile(r'[À-ỹ]')


def _fix_hyphenation(source: str) -> str:
    """Fix word-break hyphenation at line ends.

    'infor-\\nmation' -> 'information'
    'thông-\\ntin' -> 'thông tin' (Vietnamese: preserve word boundary)
    """

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        before, after = m.group(1), m.group(2)
        # Check surrounding context for Vietnamese diacritics
        ctx_start = max(0, m.start() - 15)
        ctx_end = min(len(source), m.end() + 15)
        context = source[ctx_start:ctx_end]
        if _VIETNAMESE_DIACRITICS_RE.search(context):
            return f"{before} {after}"
        return f"{before}{after}"

    return re.sub(r'([A-Za-zÀ-ỹ])-\n([A-Za-zÀ-ỹ])', _replace, source)


# Prompt-injection pattern filter — compiled once at module import.
# Applied inside `_clean_document_text` before chunking/embedding so that any
# malicious document content cannot smuggle cross-bot jailbreak instructions
# into the retrieval corpus.
# Patterns carry a leading `(?i)` inline flag, which Python 3.12's `re` no
# longer allows mid-expression after joining with `|`. Strip them and compile
# with `re.IGNORECASE` instead — behaviourally equivalent.
_INJECTION_REGEX = re.compile(
    "|".join(p.removeprefix("(?i)") for p in PROMPT_INJECTION_PATTERNS),
    re.MULTILINE | re.IGNORECASE,
)


def _strip_prompt_injection(text: str) -> tuple[str, int]:
    """Remove high-confidence prompt-injection patterns from text.

    Returns ``(cleaned_text, hit_count)``. Conservative: only filters exact
    matches of well-known jailbreak phrases. Bot-owner legitimate instructions
    live in ``bots.system_prompt``, NOT in ingested document content.
    """
    return _INJECTION_REGEX.subn("[REDACTED]", text)


def _clean_document_text(text: str) -> str:
    """Clean document text at ingest time: strip repeated headers/footers, normalize Unicode."""
    # NFC normalization preserves VN diacritic glyphs without
    # compatibility-folding technical Unicode like "①"/"㎏".
    text = normalize_vn(text)
    # Fix word-break hyphenation (before whitespace normalization)
    text = _fix_hyphenation(text)
    # Strip prompt-injection patterns BEFORE chunking + embedding
    text, injection_hits = _strip_prompt_injection(text)
    if injection_hits > 0:
        logger.warning(
            "prompt_injection_patterns_stripped",
            count=injection_hits,
            text_length=len(text),
        )
    # Collapse multiple blank lines to max 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip repeated lines (likely headers/footers)
    lines = text.split("\n")
    if len(lines) > 10:
        line_counts = Counter(line.strip() for line in lines if line.strip())
        repeated = {line for line, count in line_counts.items() if count >= 3 and len(line) < 100}
        if repeated:
            lines = [line for line in lines if line.strip() not in repeated]
    text = "\n".join(lines)
    # Normalize whitespace within lines
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# URL pattern stripped from EMBED text only (raw ``content`` keeps URLs for
# BM25 exact-match). Compiled once at module load.
_EMBED_URL_RE = re.compile(r"https?://\S+")


def canonicalize_embed_text(text: str) -> str:
    """Derive the EMBEDDING text from a raw chunk (dual-field pattern).

    Research-backed (Vespa/ES native field split + Chroma chunking study):
    embed a CLEANED canonical form while keeping the raw ``content`` for BM25
    exact-match. Two cheap, safe transforms that cut token waste + semantic
    dilution without losing keyword recall (raw is unchanged):

    1. **Strip image/file URLs** — a warehouse sheet stuffed ``drive.google.com``
       / ``lh3.google.com`` links into every row; embedding 300-char opaque
       URLs dilutes the vector and (2026-06-13) appears to stall the embedder
       on URL-heavy chunks. URLs carry zero semantic retrieval signal.
    2. **Collapse runs of whitespace** — ``"nhà     tôi   có"`` → ``"nhà tôi có"``;
       redundant spaces add tokens, not meaning.

    Only the vector input is canonicalised; the raw chunk (with URLs) is still
    persisted to ``content`` and indexed for BM25. Never returns empty.
    """
    cleaned = _EMBED_URL_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n[ \t]*\n+", "\n", cleaned)
    return cleaned.strip() or text.strip()


# AdapChunk Layer 7 — Narrate-then-Embed dispatch (Wave E3). The actual
# helpers live in :mod:`ragbot.application.services.narrate_dispatch` so
# they can be unit-tested without importing the rest of this module's
# heavy ingest pipeline. The ingest path below references them under the
# leading-underscore aliases for symmetry with other private helpers.
from ragbot.application.services.narrate_dispatch import (  # noqa: E402 — anchor near use site
    classify_chunk_block_type as _classify_chunk_block_type,  # noqa: F401 — re-export for tests + grep
    narrate_chunks_for_embed as _narrate_chunks_for_embed,
)


# M10 — map the uppercase narrate ``BlockType`` labels to the lowercase
# ``document_chunks.chunk_type`` values defined in ``constants.py``. We
# collapse HEADING / LIST / IMAGE / FORMULA → "text" because the column's
# CHECK constraint is tight (4 values) and the modality-aware rerank only
# distinguishes the four — finer-grained labels live in ``metadata_json``.
_BLOCK_TYPE_TO_CHUNK_TYPE: Final[dict[str, str]] = {
    "TABLE": DEFAULT_CHUNK_TYPE_TABLE,
    "CODE": DEFAULT_CHUNK_TYPE_CODE,
    "TEXT": DEFAULT_CHUNK_TYPE_TEXT,
    "FORMULA": DEFAULT_CHUNK_TYPE_TEXT,
    "IMAGE": DEFAULT_CHUNK_TYPE_TEXT,
}


def chunk_type_for(chunk_text: str, *, is_table_row: bool = False) -> str:
    """Return the canonical ``document_chunks.chunk_type`` for one chunk.

    ``is_table_row=True`` short-circuits the classifier (CSV / Excel row-
    per-chunk path) — heuristic detection would mis-label a row as TEXT
    when the header is on a separate line. All other chunks reuse the
    existing ``classify_chunk_block_type`` so chunking and persistence
    agree on the modality label.
    """
    if is_table_row:
        return DEFAULT_CHUNK_TYPE_TABLE_ROW
    label = _classify_chunk_block_type(chunk_text)
    return _BLOCK_TYPE_TO_CHUNK_TYPE.get(label, DEFAULT_CHUNK_TYPE_TEXT)


def should_skip_row_enrich(strategy: str, *, gate_enabled: bool) -> bool:
    """Whether per-chunk LLM enrichment should be skipped for this strategy.

    Tabular strategies (``table_csv`` / ``table_dual_index``) emit one chunk
    per data row; each row already carries its header + key:value structure,
    so the Anthropic-CR / legacy-enrich recall lift is ~0 on them while the
    per-chunk LLM call count dominates ingest latency/cost. When the gate is
    on AND the resolved strategy is tabular, all per-chunk enrichment paths
    skip. Pure function so the ingest gate is unit-testable in isolation.
    """
    return gate_enabled and strategy in CR_ROW_GATED_STRATEGIES


__all__ = [
    "_VIETNAMESE_DIACRITICS_RE",
    "_fix_hyphenation",
    "_INJECTION_REGEX",
    "_strip_prompt_injection",
    "_clean_document_text",
    "_EMBED_URL_RE",
    "canonicalize_embed_text",
    "_BLOCK_TYPE_TO_CHUNK_TYPE",
    "chunk_type_for",
    "should_skip_row_enrich",
]
