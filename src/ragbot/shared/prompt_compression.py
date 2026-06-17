"""Lightweight prompt compression for RAG chunks.

Reduces token usage in the generate node by removing redundant whitespace,
boilerplate, and low-information sentences. No external dependencies, no GPU.
Language-aware stop-word + boilerplate filtering resolved from a 4-tier chain:

    1. per-bot `bots.custom_vocabulary.{boilerplate_patterns, stopwords}`
       (caller passes ``custom_patterns`` / ``custom_stopwords`` kwarg)
    2. tenant-global `system_config.boilerplate_removal_patterns_by_language[lang]`
       and `system_config.stopwords_by_language[lang]` (caller injects via
       ``system_config_patterns`` / ``system_config_stopwords`` kwarg)
    3. boot-fallback constants in ``shared/constants.py`` (when language="vi")
    4. empty (other languages without explicit config — no-op)

This is a CPU-only alternative to LLMLingua-style neural compression.
"""

from __future__ import annotations

import re
from typing import Any

from ragbot.shared.constants import (
    DEFAULT_BOILERPLATE_PATTERNS_VI,
    DEFAULT_LANGUAGE,
    DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    DEFAULT_VI_STOPWORDS,
)

# ── Negation words — NEVER treat as stop words (they flip meaning) ───────────
# These are universal sentence-scoring signal, not domain config: removing them
# silently corrupts answer accuracy on negated claims regardless of language.
_NEGATION_WORDS: frozenset[str] = frozenset(
    "không chưa chẳng đừng hết thiếu trừ".split()
)

# Multi-word negation phrases checked in sentence scoring
_NEGATION_PHRASES: tuple[str, ...] = (
    "chưa từng", "không có", "không được", "ngoại trừ",
)

# Multiple whitespace / blank lines
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)

# Compile cache: (language, custom_hash) → compiled patterns / stopwords frozenset
# Keyed by hashable tuple so DB-driven overrides recompile only when contents change.
_pattern_cache: dict[tuple[str, int], list[re.Pattern[str]]] = {}
_stopword_cache: dict[tuple[str, int], frozenset[str]] = {}


def _resolve_patterns(
    language: str,
    custom_patterns: list[str] | tuple[str, ...] | None,
    system_config_patterns: dict[str, list[str]] | None,
) -> list[re.Pattern[str]]:
    """4-tier resolve + compile for boilerplate regex patterns.

    Tier 1: ``custom_patterns`` (per-bot, caller-supplied) when non-empty.
    Tier 2: ``system_config_patterns[language]`` (tenant-global) when non-empty.
    Tier 3: ``DEFAULT_BOILERPLATE_PATTERNS_VI`` only when ``language == "vi"``.
    Tier 4: empty list (other languages with no override → no-op removal).
    """
    raw: tuple[str, ...]
    if custom_patterns:
        raw = tuple(custom_patterns)
    elif system_config_patterns and system_config_patterns.get(language):
        raw = tuple(system_config_patterns[language])
    elif language == "vi":
        raw = DEFAULT_BOILERPLATE_PATTERNS_VI
    else:
        return []

    cache_key = (language, hash(raw))
    cached = _pattern_cache.get(cache_key)
    if cached is not None:
        return cached

    compiled: list[re.Pattern[str]] = []
    for src in raw:
        # ^…$ patterns need MULTILINE; everything else uses IGNORECASE so the
        # boot defaults match the original behaviour byte-for-byte. Caller can
        # author overrides with inline `(?i)` / `(?m)` flags as needed.
        flags = re.MULTILINE if src.startswith("^") else re.IGNORECASE
        try:
            compiled.append(re.compile(src, flags))
        except re.error:
            # Bad operator-authored regex must not crash the pipeline — skip it.
            continue
    _pattern_cache[cache_key] = compiled
    return compiled


def _resolve_stopwords(
    language: str,
    custom_stopwords: list[str] | tuple[str, ...] | None,
    system_config_stopwords: dict[str, list[str]] | None,
) -> frozenset[str]:
    """4-tier resolve for sentence-scoring stop words. Negation words filtered out."""
    raw: tuple[str, ...]
    if custom_stopwords:
        raw = tuple(custom_stopwords)
    elif system_config_stopwords and system_config_stopwords.get(language):
        raw = tuple(system_config_stopwords[language])
    elif language == "vi":
        raw = DEFAULT_VI_STOPWORDS
    else:
        return frozenset()

    cache_key = (language, hash(raw))
    cached = _stopword_cache.get(cache_key)
    if cached is not None:
        return cached
    resolved = frozenset(w for w in raw if w not in _NEGATION_WORDS)
    _stopword_cache[cache_key] = resolved
    return resolved


def _normalize_whitespace(text: str) -> str:
    """Collapse redundant whitespace while preserving paragraph breaks."""
    text = _TRAILING_SPACE_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _remove_boilerplate(text: str, patterns: list[re.Pattern[str]]) -> str:
    """Remove common boilerplate lines using the supplied compiled patterns."""
    for pat in patterns:
        text = pat.sub("", text)
    return text


def _remove_markdown_artifacts(text: str) -> str:
    """Strip markdown formatting, keeping readable text."""
    # Replace links [text](url) with just text
    text = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)
    # Remove images, bold/italic markers, strikethrough
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\*{1,3}|_{1,3}|~{2}", "", text)
    # Remove header markers but keep text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text


def _sentence_info_score(
    sentence: str,
    *,
    stopwords: frozenset[str] | None = None,
) -> float:
    """Score a sentence by information density (0.0 = all stop words, 1.0 = all content).

    Sentences with numbers, prices, dates, proper nouns score higher.
    ``stopwords`` defaults to the boot Vietnamese set for backward-compat
    with direct callers (tests, scripts).
    """
    if stopwords is None:
        stopwords = _resolve_stopwords(DEFAULT_LANGUAGE, None, None)

    words = re.findall(r"[\w\d]+", sentence.lower())
    if not words:
        return 0.0

    content_words = [w for w in words if w not in stopwords and len(w) > 1]
    base_score = len(content_words) / len(words) if words else 0.0

    # Bonus for high-value tokens: numbers, prices, dates, percentages
    has_number = bool(re.search(r"\d", sentence))
    has_currency = bool(re.search(r"[\$€₫]|\bVND\b|\bUSD\b|\btriệu\b|\btỷ\b|\bnghìn\b", sentence, re.IGNORECASE))
    has_date = bool(re.search(r"\d{1,2}[/\-\.]\d{1,2}|\d{4}", sentence))
    has_percent = bool(re.search(r"\d+\s*%", sentence))

    bonus = 0.0
    if has_number:
        bonus += 0.1
    if has_currency:
        bonus += 0.15
    if has_date:
        bonus += 0.1
    if has_percent:
        bonus += 0.1

    # Bonus for negation words (critical for accuracy — they flip meaning)
    sentence_lower = sentence.lower()
    if any(neg in sentence_lower for neg in _NEGATION_PHRASES) or \
       any(w in _NEGATION_WORDS for w in words):
        bonus += 0.2

    return min(base_score + bonus, 1.0)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling Vietnamese punctuation."""
    # Split on sentence-ending punctuation followed by space or newline
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    return [s.strip() for s in parts if s.strip()]


def _smart_truncate(text: str, max_chars: int) -> str:
    """Truncate text at sentence boundary, staying under max_chars."""
    if len(text) <= max_chars:
        return text

    sentences = _split_sentences(text)
    result_parts: list[str] = []
    current_len = 0

    for sent in sentences:
        added_len = len(sent) + (1 if result_parts else 0)  # +1 for space
        if current_len + added_len > max_chars:
            break
        result_parts.append(sent)
        current_len += added_len

    # If even the first sentence exceeds max_chars, hard-truncate it
    if not result_parts and sentences:
        return sentences[0][:max_chars]

    return " ".join(result_parts)


def compress_chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    remove_boilerplate: bool = True,
    remove_markdown: bool = True,
    preserve_key_info: bool = True,
    min_sentence_score: float = 0.15,
    language: str = DEFAULT_LANGUAGE,
    custom_patterns: list[str] | tuple[str, ...] | None = None,
    custom_stopwords: list[str] | tuple[str, ...] | None = None,
    system_config_patterns: dict[str, list[str]] | None = None,
    system_config_stopwords: dict[str, list[str]] | None = None,
) -> str:
    """Compress a single chunk's text content.

    Steps:
    1. Normalize whitespace
    2. Optionally remove boilerplate lines (4-tier resolved patterns)
    3. Optionally strip markdown artifacts
    4. Score sentences by information density (4-tier resolved stopwords)
    5. Smart-truncate to max_chars at sentence boundary
    """
    if not text:
        return text

    patterns = _resolve_patterns(language, custom_patterns, system_config_patterns)
    stopwords = _resolve_stopwords(language, custom_stopwords, system_config_stopwords)

    # Step 1: whitespace
    result = _normalize_whitespace(text)

    # Step 2: boilerplate
    if remove_boilerplate and patterns:
        result = _remove_boilerplate(result, patterns)
        result = _normalize_whitespace(result)

    # Step 3: markdown
    if remove_markdown:
        result = _remove_markdown_artifacts(result)
        result = _normalize_whitespace(result)

    # Step 4: sentence filtering (only if over limit)
    if preserve_key_info and len(result) > max_chars:
        sentences = _split_sentences(result)
        if len(sentences) > 1:
            scored = [(s, _sentence_info_score(s, stopwords=stopwords)) for s in sentences]
            # Keep sentences above minimum score threshold
            kept = [s for s, score in scored if score >= min_sentence_score]
            if kept:
                result = " ".join(kept)
            else:
                # All sentences below threshold — keep top 50% by score
                scored.sort(key=lambda x: x[1], reverse=True)
                top_half = max(1, len(scored) // 2)
                # Restore original order for kept sentences
                top_sentences = scored[:top_half]
                top_sentences.sort(key=lambda x: sentences.index(x[0]))
                result = " ".join(s for s, _sc in top_sentences)

    # Step 5: truncate
    result = _smart_truncate(result, max_chars)

    return result


def _is_full_document_chunk(chunk: dict[str, Any]) -> bool:
    """True if chunk was stored whole (preserve_full_doc strategy)."""
    if chunk.get("is_full_document"):
        return True
    meta = chunk.get("metadata") or {}
    return bool(meta.get("is_full_document"))


def _looks_tabular(text: str) -> bool:
    """Heuristic: ≥2 lines look like markdown table or CSV rows."""
    if not text:
        return False
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    pipe_rows = sum(1 for l in lines if l.count("|") >= 2)
    csv_rows = sum(1 for l in lines if l.count(",") >= 2)
    return pipe_rows >= 2 or csv_rows >= 2


def compress_chunks(
    chunks: list[dict[str, Any]],
    *,
    max_chars_per_chunk: int = DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    remove_boilerplate: bool = True,
    preserve_key_info: bool = True,
    language: str = DEFAULT_LANGUAGE,
    custom_patterns: list[str] | tuple[str, ...] | None = None,
    custom_stopwords: list[str] | tuple[str, ...] | None = None,
    system_config_patterns: dict[str, list[str]] | None = None,
    system_config_stopwords: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Compress chunk contents to reduce token usage in generate node.

    Bypasses compression for chunks marked ``is_full_document`` (whole-doc
    strategy from ingest) and chunks that look tabular (CSV / pipe table) —
    the sentence-split + score-truncate pipeline destroys atomic rows
    (price tables, FAQ lists). Returns chunks with compressed
    ``content``/``text`` fields; original content preserved in
    ``original_content`` if modified.

    ``language`` + ``custom_*`` + ``system_config_*`` kwargs feed the 4-tier
    boilerplate/stopword resolver in ``_resolve_patterns`` /
    ``_resolve_stopwords``. Default keeps backward-compat behaviour for
    legacy callers (Vietnamese boot defaults).
    """
    compressed: list[dict[str, Any]] = []

    for chunk in chunks:
        original_text = chunk.get("content") or chunk.get("text") or ""
        if not original_text:
            compressed.append(chunk)
            continue

        if _is_full_document_chunk(chunk) or _looks_tabular(original_text):
            compressed.append(chunk)
            continue

        new_text = compress_chunk_text(
            original_text,
            max_chars=max_chars_per_chunk,
            remove_boilerplate=remove_boilerplate,
            preserve_key_info=preserve_key_info,
            language=language,
            custom_patterns=custom_patterns,
            custom_stopwords=custom_stopwords,
            system_config_patterns=system_config_patterns,
            system_config_stopwords=system_config_stopwords,
        )

        if new_text != original_text:
            chunk_copy = {**chunk, "original_content": original_text}
            if "content" in chunk:
                chunk_copy["content"] = new_text
            if "text" in chunk:
                chunk_copy["text"] = new_text
            compressed.append(chunk_copy)
        else:
            compressed.append(chunk)

    return compressed


__all__ = ["compress_chunks", "compress_chunk_text"]
