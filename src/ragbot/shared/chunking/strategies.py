"""Chunking strategy implementations: recursive / HDT / semantic / proposition / hybrid.

Extracted from the chunking god-file: each strategy turns cleaned text into a
chunk list; smart_chunk (core) dispatches to these. Re-exported by chunking/__init__."""
from __future__ import annotations

import re
from typing import Any

import structlog

from ragbot.shared.bootstrap_config import get_boot_config  # noqa: F401
from ragbot.shared.constants import *  # noqa: F401,F403
from ragbot.shared.chunking.vn_structural import *  # noqa: F401,F403
from ragbot.shared.chunking.analyze import *  # noqa: F401,F403
from ragbot.shared.chunking.blocks import *  # noqa: F401,F403
from ragbot.shared.chunking.csv_chunker import *  # noqa: F401,F403

logger = structlog.get_logger(__name__)

_STRUCTURAL_PATH_RE = re.compile(r"^\[(?P<path>[^\]]+)\]\n?")


def extract_structural_path(chunk: str) -> dict[str, Any]:
    """Extract ``[Chapter > Section]`` prefix from an HDT chunk.

    Returns ``{"content": ..., "structural_path": {"full": ..., "parts": [...]}}``
    when a path is present, otherwise ``{"content": chunk, "structural_path": None}``.
    """
    m = _STRUCTURAL_PATH_RE.match(chunk)
    if m:
        full = m.group("path").strip()
        parts = [p.strip() for p in full.split(">") if p.strip()]
        content = chunk[m.end():]
        return {
            "content": content,
            "structural_path": {"full": full, "parts": parts},
        }
    return {"content": chunk, "structural_path": None}


def _split_h1_sections(text: str) -> list[str]:
    """Split markdown text at H1 boundaries (line beginning with ``# ``).

    Stream A Phase 3 — H1 acts as a hard chunk break point regardless of
    ``chunk_size``. A document with two H1 sections must produce ≥ 2
    chunks even when both fit inside the configured chunk_size, otherwise
    retrieval for queries scoped to one section can pull in unrelated
    content from the other.
    """
    parts: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.startswith("# ") and current and any(c.strip() for c in current):
            parts.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current and any(c.strip() for c in current):
        parts.append("\n".join(current))
    return parts or [text]


def _chunk_recursive_with_tables(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    *,
    complexity_sizing_enabled: bool = False,
    complexity_min_chunk_size: int | None = None,
    complexity_max_chunk_size: int | None = None,
    complexity_measure: str = "combined",
    bot_id: str | None = None,
) -> list[str]:
    """Recursive chunking with table protection — default strategy.

    Pre-splits at H1 boundaries before recursive split so heading
    sections never share a chunk even when content fits in chunk_size.

    Databricks adaptive complexity sizing: when
    ``complexity_sizing_enabled=True``, the caller-provided ``chunk_size``
    is REPLACED by a per-document adaptive size derived from
    :func:`~ragbot.shared.complexity_sizing.compute_complexity`. Complex
    text gets smaller chunks. Telemetry emitted via structlog with
    ``step_name="databricks_complexity"`` so per-step latency / chunk-size
    distribution analytics can attribute behaviour to the feature flag.
    """
    # Databricks adaptive complexity sizing.
    if complexity_sizing_enabled:
        # Imported lazily to keep the no-flag hot path free of the import.
        from ragbot.shared.complexity_sizing import (
            adaptive_chunk_size,
            compute_complexity,
        )
        from ragbot.shared.constants import (
            DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE,
            DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE,
        )

        min_size = (
            complexity_min_chunk_size
            if complexity_min_chunk_size is not None
            else DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE
        )
        max_size = (
            complexity_max_chunk_size
            if complexity_max_chunk_size is not None
            else DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE
        )
        complexity = compute_complexity(text, measure=complexity_measure)  # type: ignore[arg-type]
        adaptive_size = adaptive_chunk_size(complexity, min_size, max_size)
        logger.info(
            "databricks_complexity_sizing_applied",
            step_name="databricks_complexity",
            feature_flag="databricks_complexity_sizing_enabled",
            bot_id=bot_id,
            measure=complexity_measure,
            complexity=round(complexity, 4),
            min_size=min_size,
            max_size=max_size,
            adaptive_chunk_size=adaptive_size,
            original_chunk_size=chunk_size,
        )
        chunk_size = adaptive_size

    from langchain_text_splitters import RecursiveCharacterTextSplitter

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    out: list[str] = []
    for h1_section in _split_h1_sections(text):
        blocks = _split_into_blocks(h1_section)
        chunks: list[str] = []

        for block_type, content in blocks:
            if block_type == "table":
                # ATOMIC: giữ nguyên bảng, không cắt
                if len(content) > chunk_size * 3:
                    # Bảng quá lớn (>3x chunk_size) → split theo nhóm hàng, giữ header
                    table_lines = content.split("\n")
                    header_lines = []
                    data_lines = []
                    for i, line in enumerate(table_lines):
                        if i < 2 or re.match(r"^\s*[|][\s\-:]+[|]", line.strip()):
                            header_lines.append(line)
                        else:
                            data_lines.append(line)

                    header = "\n".join(header_lines)
                    group: list[str] = []
                    group_size = len(header) + 1

                    for data_line in data_lines:
                        if group_size + len(data_line) + 1 > chunk_size and group:
                            chunks.append(header + "\n" + "\n".join(group))
                            group = []
                            group_size = len(header) + 1
                        group.append(data_line)
                        group_size += len(data_line) + 1

                    if group:
                        chunks.append(header + "\n" + "\n".join(group))
                else:
                    chunks.append(content)
            else:
                # TEXT: split bình thường
                if len(content) <= chunk_size:
                    chunks.append(content)
                else:
                    text_chunks = text_splitter.split_text(content)
                    chunks.extend(text_chunks)

        out.extend(c.strip() for c in chunks if c.strip())

    return out


_H1_LINE_RE = re.compile(r"^(# +.+?)\s*$", re.MULTILINE)
_H2_LINE_RE = re.compile(r"^(## +.+?)\s*$", re.MULTILINE)


class _HeadingIndex:
    """One-pass H1/H2 offset map for fast parent-heading lookup.

    Replaces the per-chunk ``re.findall`` + ``str.find`` scan with a
    single document walk. Each chunk lookup is O(log N) via
    ``bisect`` over the pre-built heading offset arrays — total
    O(M + K·log N) for K chunks over a doc of length M with N
    headings, down from the previous O(K·M).

    Construction is lazy at the call site so docs that never need
    ``with_metadata=True`` pay nothing.
    """

    __slots__ = ("_text", "_h1_offsets", "_h1_titles", "_h2_offsets", "_h2_titles")

    def __init__(self, text: str) -> None:
        self._text = text
        h1_offsets: list[int] = []
        h1_titles: list[str] = []
        for m in _H1_LINE_RE.finditer(text):
            h1_offsets.append(m.start())
            h1_titles.append(m.group(1).strip())
        h2_offsets: list[int] = []
        h2_titles: list[str] = []
        for m in _H2_LINE_RE.finditer(text):
            h2_offsets.append(m.start())
            h2_titles.append(m.group(1).strip())
        self._h1_offsets = h1_offsets
        self._h1_titles = h1_titles
        self._h2_offsets = h2_offsets
        self._h2_titles = h2_titles

    def parents_for_chunk(self, chunk: str) -> list[str]:
        """Return the H1/H2 stack covering ``chunk``.

        Best-effort string match: when the chunk text doesn't appear
        verbatim in the source (e.g. after recursive overlap rewrites),
        returns an empty stack — same semantic as the previous
        ``_resolve_parent_headings`` shape.
        """
        fingerprint = chunk[:80] if len(chunk) >= 80 else chunk
        idx = self._text.find(fingerprint)
        if idx < 0:
            return []
        cover_end = idx + len(chunk)
        stack: list[str] = []
        # H1: most-recent heading at or before ``cover_end``.
        # bisect_right gives the count of headings with offset < cover_end+1
        # — wrap as: pos = bisect_right(h1_offsets, cover_end) − 1.
        from bisect import bisect_right

        pos = bisect_right(self._h1_offsets, cover_end) - 1
        if pos >= 0:
            stack.append(self._h1_titles[pos])
        # H2: every H2 in [0, cover_end]. With H1 hard-break upstream this
        # collection stays scoped to a single H1.
        h2_end = bisect_right(self._h2_offsets, cover_end)
        seen: set[str] = set()
        for i in range(h2_end):
            title = self._h2_titles[i]
            if title not in seen:
                seen.add(title)
                stack.append(title)
        return stack


def _resolve_parent_headings(text: str, chunk: str) -> list[str]:
    """Build the (H1, H2) heading stack covering ``chunk``.

    Thin wrapper around :class:`_HeadingIndex` that preserves the
    original per-call signature for existing tests / callers. For
    multi-chunk loops, callers SHOULD build a single ``_HeadingIndex``
    and reuse it across chunks — see ``smart_chunk`` which does so.

    Walks the original ``text`` up to and including the chunk's body so
    a section heading that lives at the top of the chunk itself (e.g.
    ``## Triệt lông`` followed by the chunk's pricing rows) still
    surfaces as a parent — semantically it's the breadcrumb for
    everything below it.

    Best-effort string match: if the chunk text doesn't appear verbatim
    (e.g. after recursive overlap rewrites), returns an empty stack.
    """
    return _HeadingIndex(text).parents_for_chunk(chunk)


# ---------------------------------------------------------------------------
# Strategy: HDT (Heading Document Tree)
# ---------------------------------------------------------------------------


def _chunk_hdt(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """HDT — chunk theo heading hierarchy, giữ structural path.

    Phù hợp: tài liệu có mục lục, báo cáo, luận văn.
    """
    lines = text.split("\n")
    chunks: list[str] = []
    current_section: list[str] = []
    current_path: list[str] = []
    # Absolute markdown level (1/2/3) of each entry in current_path. Tracked
    # separately so pops compare against the heading LEVEL, not path length —
    # otherwise a skipped level (e.g. H1 → H3 with no H2) makes same-level
    # siblings nest under each other.
    current_levels: list[int] = []

    for line in lines:
        stripped = line.strip()

        # Detect heading level
        heading_level = 0
        if stripped.startswith("### "):
            heading_level = 3
        elif stripped.startswith("## "):
            heading_level = 2
        elif stripped.startswith("# "):
            heading_level = 1

        if heading_level > 0:
            # Flush current section
            if current_section:
                content = "\n".join(current_section).strip()
                if content:
                    path_str = " > ".join(current_path) if current_path else ""
                    if path_str:
                        content = f"[{path_str}]\n{content}"
                    chunks.append(content)
                current_section = []

            # Update path — pop every heading at the same-or-deeper level so a
            # new heading replaces siblings rather than nesting beneath them.
            heading_text = stripped.lstrip("# ").strip()
            while current_levels and current_levels[-1] >= heading_level:
                current_levels.pop()
                current_path.pop()
            current_path.append(heading_text)
            current_levels.append(heading_level)
        else:
            current_section.append(line)

    # Flush last section
    if current_section:
        content = "\n".join(current_section).strip()
        if content:
            path_str = " > ".join(current_path) if current_path else ""
            if path_str:
                content = f"[{path_str}]\n{content}"
            chunks.append(content)

    # Split oversized chunks
    final_chunks: list[str] = []
    for chunk in chunks:
        if len(chunk) > chunk_size * 2:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            # Extract path before splitting so we can re-prepend
            parsed = extract_structural_path(chunk)
            clean_text = parsed["content"]
            path_info = parsed["structural_path"]

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size, chunk_overlap=DEFAULT_CHUNK_OVERLAP_BOUNDARY,
            )
            sub_chunks = splitter.split_text(clean_text)
            if path_info:
                prefix = f"[{path_info['full']}]\n"
                sub_chunks = [prefix + sc for sc in sub_chunks]
            final_chunks.extend(sub_chunks)
        else:
            final_chunks.append(chunk)

    return [c.strip() for c in final_chunks if c.strip()]


# ---------------------------------------------------------------------------
# Strategy: Semantic (paragraph-based)
# ---------------------------------------------------------------------------


def _sentence_similarity(s1: str, s2: str) -> float:
    """Sentence similarity using SequenceMatcher + word Jaccard (blended).

    Thin wrapper that delegates to :func:`ragbot.shared.sentence_similarity.
    lexical_similarity`. Kept in this module so legacy callers
    (``_chunk_semantic`` sync path) retain the same import surface; the
    actual numeric blend is owned by the shared module so the lexical
    strategy class and this helper cannot drift apart.
    """
    from ragbot.shared.sentence_similarity import lexical_similarity

    return lexical_similarity(s1, s2)


_ABBREVIATIONS = frozenset({
    "tp", "ts", "ths", "gs", "pgs", "cn", "bs", "ks",
    "stt", "đt", "đc", "sl", "ht", "nxb", "vd", "vv",
    # Titles / generic abbreviations whose trailing dot is NOT a sentence end
    # (audit 2026-06-13: "Dr. Medispa" was split into two sentences). Kept
    # domain-neutral — common across verticals, not tenant-specific.
    "dr", "mr", "mrs", "ms", "no", "vs",
})


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences — handles Vietnamese abbreviations."""
    # Protect known abbreviations from splitting
    protected = text
    for abbr in _ABBREVIATIONS:
        protected = re.sub(
            rf'\b({abbr})\.\s',
            rf'\1<DOT> ',
            protected,
            flags=re.IGNORECASE,
        )
    # Protect numbered lists: "1." "2." etc
    protected = re.sub(r'(\d+)\.\s', r'\1<DOT> ', protected)
    # Protect common patterns: "v.v." "etc." — keep trailing dot as sentence ender
    protected = re.sub(r'\bv\.v\.(?=\s)', 'v<DOT>v.', protected)
    # "etc." mid-sentence is rarely a real boundary regardless of the next
    # word's case (audit 2026-06-13: requiring a following capital wrongly
    # split "etc. and ..." / Vietnamese lowercase continuations).
    protected = re.sub(r'\betc\.(?=\s)', 'etc<DOT>', protected)

    # Split at real sentence boundaries
    parts = re.split(r'(?<=[.!?])\s+', protected)

    # Restore protected dots
    return [s.replace('<DOT>', '.').strip() for s in parts if s.strip()]


def _chunk_semantic(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    similarity_threshold: float = DEFAULT_SEMANTIC_SIMILARITY_THRESHOLD,
) -> list[str]:
    """Semantic chunking: split at topic boundaries using sentence similarity.

    1. Split text into sentences
    2. Compute similarity between adjacent sentences
    3. Split where similarity < threshold (topic shift)
    4. Group sentences into chunks respecting chunk_size

    Phù hợp: văn xuôi dài, sách giáo khoa, bài báo.
    """
    if not text or not text.strip():
        return []

    # Step 1: Split into sentences
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text.strip()] if text.strip() else []

    # Step 2: Compute adjacent similarities
    similarities = [
        _sentence_similarity(sentences[i], sentences[i + 1])
        for i in range(len(sentences) - 1)
    ]

    # Step 3: Find split points (low similarity = topic boundary)
    split_indices = [
        i + 1 for i, sim in enumerate(similarities)
        if sim < similarity_threshold
    ]

    # Step 4: Group sentences into segments
    segments: list[str] = []
    prev = 0
    for idx in split_indices:
        segment = " ".join(sentences[prev:idx]).strip()
        if segment:
            segments.append(segment)
        prev = idx
    # Last segment
    last = " ".join(sentences[prev:]).strip()
    if last:
        segments.append(last)

    # Step 5: Respect chunk_size — split oversized segments
    chunks: list[str] = []
    for segment in segments:
        if len(segment) <= chunk_size:
            chunks.append(segment)
        else:
            # Sub-split large segments with overlap
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP_BOUNDARY,
            )
            chunks.extend(splitter.split_text(segment))

    return chunks


# ---------------------------------------------------------------------------
# Strategy: Embedding-based Semantic (async — fix gap S1)
# ---------------------------------------------------------------------------


async def _chunk_semantic_embed(
    text: str,
    *,
    similarity_port: Any,  # SentenceSimilarityPort — runtime import (cycle-break)
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    similarity_threshold: float | None = None,
    max_sentences: int | None = None,
) -> list[str]:
    """Embedding-based semantic chunking — async cousin of :func:`_chunk_semantic`.

    Splits ``text`` at points where adjacent-sentence similarity (scored by
    ``similarity_port``) drops below ``similarity_threshold``. Identical
    grouping + sub-split logic to the sync legacy strategy; the only delta
    is how the per-pair score is computed.

    Why a separate function (not an ``async`` rewrite of ``_chunk_semantic``):

    * Dozens of sync callers (``smart_chunk``, ``document_service``)
      depend on the sync signature. Forcing them all async on a
      feature-flagged change violates Surgical Changes (CLAUDE.md).
    * The baseline ``_chunk_semantic`` still runs when the operator has
      not opted into embedding chunking — its behaviour must be
      preserved bit-for-bit for the rollback path.

    Telemetry
    ---------
    Emits ``semantic_chunk_embed`` structlog event with provider name,
    sentence count, segments produced, average cosine similarity, and the
    similarity-port stats snapshot (cache hits / misses).

    Proof citation
    --------------
    LangChain ``SemanticChunker`` boundary algorithm; NVIDIA RAGAS
    page-level recall benchmark (0.51 → 0.65 on narrative documents
    when switching from lexical to embedding cosine). Closes the gap
    where the baseline semantic strategy scores Vietnamese paraphrase
    pairs near 0.0 lexically and over-segments topic-coherent paragraphs.
    """
    from ragbot.shared.constants import (
        DEFAULT_EMBEDDING_SEMANTIC_MAX_SENTENCES,
        DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD,
    )

    if not text or not text.strip():
        return []

    threshold = (
        DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD
        if similarity_threshold is None
        else float(similarity_threshold)
    )
    max_sent = (
        DEFAULT_EMBEDDING_SEMANTIC_MAX_SENTENCES
        if max_sentences is None
        else int(max_sentences)
    )

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text.strip()] if text.strip() else []

    # Safety budget: when a doc explodes into 10k+ sentences the embed
    # provider bill outweighs any recall lift. Fall back to lexical so
    # ingest still completes deterministically.
    if len(sentences) > max_sent:
        provider_name = getattr(similarity_port, "provider_name", "unknown")
        logger.warning(
            "semantic_chunk_embed_sentence_overflow",
            n_sentences=len(sentences),
            max_sentences=max_sent,
            provider=provider_name,
            fallback="lexical_chunk_semantic",
        )
        return _chunk_semantic(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            similarity_threshold=DEFAULT_SEMANTIC_SIMILARITY_THRESHOLD,
        )

    similarities: list[float] = []
    for i in range(len(sentences) - 1):
        score = await similarity_port.similarity(sentences[i], sentences[i + 1])
        similarities.append(float(score))

    split_indices = [
        i + 1 for i, sim in enumerate(similarities) if sim < threshold
    ]

    segments: list[str] = []
    prev = 0
    for idx in split_indices:
        segment = " ".join(sentences[prev:idx]).strip()
        if segment:
            segments.append(segment)
        prev = idx
    last = " ".join(sentences[prev:]).strip()
    if last:
        segments.append(last)

    chunks: list[str] = []
    for segment in segments:
        if len(segment) <= chunk_size:
            chunks.append(segment)
        else:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP_BOUNDARY,
            )
            chunks.extend(splitter.split_text(segment))

    avg_sim = (sum(similarities) / len(similarities)) if similarities else 0.0
    port_stats: dict[str, Any] = {}
    stats_fn = getattr(similarity_port, "stats", None)
    if callable(stats_fn):
        try:
            port_stats = stats_fn() or {}
        except Exception:  # noqa: BLE001 — stats is observability-only, never block ingest
            port_stats = {}

    logger.info(
        "semantic_chunk_embed",
        step_name="semantic_chunk_embed",
        feature_flag="embedding_semantic_chunk_enabled",
        provider=getattr(similarity_port, "provider_name", "unknown"),
        n_sentences=len(sentences),
        n_segments=len(segments),
        n_chunks=len(chunks),
        avg_cosine_sim=round(avg_sim, 4),
        similarity_threshold=threshold,
        **{f"port_{k}": v for k, v in port_stats.items()},
    )

    return chunks


# ---------------------------------------------------------------------------
# Strategy: Proposition (atomic self-contained statements)
# ---------------------------------------------------------------------------


def _chunk_proposition(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Proposition-based chunking: split into atomic self-contained statements.

    Each proposition is a single factual claim that makes sense without context.
    Uses rule-based splitting (sentence boundaries + clause detection).
    For LLM-based proposition extraction, use the async enrichment pipeline.
    """
    if not text or not text.strip():
        return []

    # Split into sentences
    sentences = _split_sentences(text)
    if not sentences:
        return []

    # Further split compound sentences at clause boundaries.
    #
    # Split ONLY at COORDINATING connectors (independent clauses joined by
    # và/hoặc/nhưng/and/or/but…) — splitting there yields two self-contained
    # facts. SUBORDINATING connectors (khi/nếu/vì/mà/do đó/vì vậy/tuy/dù/
    # mặc dù) were removed (P2-B bug-D): the non-capturing group consumed
    # them, so "A, nếu B" decomposed into "A" and "B" with the condition
    # severed — turning a conditional fact into a false unconditional claim.
    # A conditional/causal clause must stay attached to its main clause.
    propositions = []
    for sent in sentences:
        clauses = re.split(
            r'(?:;\s*|\s*\u2014\s*'
            r'|,\s*(?:và|hoặc|nhưng|tuy nhiên|ngoài ra|đồng thời|bên cạnh đó)'
            r'|,\s*(?:and|or|but|however|moreover|additionally))',
            sent,
        )
        for clause in clauses:
            clause = clause.strip()
            if len(clause) > DEFAULT_CHUNK_MIN_CLAUSE_LEN:  # skip tiny fragments
                propositions.append(clause)

    if not propositions:
        return [text.strip()] if text.strip() else []

    # Group propositions into chunks respecting chunk_size
    chunks = []
    current = []
    current_len = 0
    for prop in propositions:
        if current_len + len(prop) > chunk_size and current:
            chunks.append(". ".join(current))
            # Overlap: keep last proposition
            if chunk_overlap > 0 and current:
                current = [current[-1]]
                current_len = len(current[0])
            else:
                current = []
                current_len = 0
        current.append(prop)
        current_len += len(prop) + 2  # ". " separator
    if current:
        chunks.append(". ".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Strategy: Hybrid (HDT macro + PROPOSITION micro)
# ---------------------------------------------------------------------------


def _chunk_hybrid(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    proposition_threshold: int = DEFAULT_PROPOSITION_THRESHOLD_WORDS,
) -> list[str]:
    """Hybrid chunking: HDT macro-structure + PROPOSITION micro-structure.

    1. HDT pass: split by headings into sections
    2. For sections > proposition_threshold words: apply PROPOSITION
    3. For smaller sections: keep as-is
    """
    if not text or not text.strip():
        return []

    # Step 1: HDT to get sections
    hdt_chunks = _chunk_hdt(text, chunk_size * 2)  # larger chunks for macro

    # Step 2: Apply PROPOSITION to large sections
    result = []
    for chunk in hdt_chunks:
        # Strip structural path prefix before proposition splitting
        parsed = extract_structural_path(chunk)
        clean_text = parsed["content"]
        path_info = parsed["structural_path"]

        word_count = len(clean_text.split())
        if word_count > proposition_threshold:
            # Large section → PROPOSITION micro-chunking
            sub_chunks = _chunk_proposition(clean_text, chunk_size, chunk_overlap)
            # Re-prepend path to each sub-chunk
            if path_info:
                prefix = f"[{path_info['full']}]\n"
                sub_chunks = [prefix + sc for sc in sub_chunks]
            result.extend(sub_chunks)
        else:
            # Small section → keep as-is (preserve original chunk with path)
            if len(chunk) > chunk_size:
                # Still too large for output → recursive split
                from langchain_text_splitters import RecursiveCharacterTextSplitter
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                )
                sub_chunks = splitter.split_text(clean_text)
                if path_info:
                    prefix = f"[{path_info['full']}]\n"
                    sub_chunks = [prefix + sc for sc in sub_chunks]
                result.extend(sub_chunks)
            else:
                result.append(chunk)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


__all__ = [
    "extract_structural_path",
    "_STRUCTURAL_PATH_RE",
    "_split_h1_sections",
    "_chunk_recursive_with_tables",
    "_H1_LINE_RE",
    "_H2_LINE_RE",
    "_HeadingIndex",
    "_resolve_parent_headings",
    "_chunk_hdt",
    "_sentence_similarity",
    "_ABBREVIATIONS",
    "_split_sentences",
    "_chunk_semantic",
    "_chunk_semantic_embed",
    "_chunk_proposition",
    "_chunk_hybrid",
]
