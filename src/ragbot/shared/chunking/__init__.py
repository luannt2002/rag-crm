"""Dynamic chunking — auto-detect document type và chọn strategy phù hợp.

Luồng:
1. analyze_document() — phân tích cấu trúc (headings, tables, text length)
2. select_strategy() — rule-based chọn strategy: hdt | semantic | recursive | hybrid
3. smart_chunk() — dispatch tới strategy tương ứng

Strategies:
- hdt: Heading Document Tree — tài liệu có cấu trúc rõ (mục lục, heading hierarchy)
- semantic: paragraph-based — văn xuôi dài, ít heading
- recursive: table-aware recursive — default, bảo vệ bảng + code blocks
- proposition: atomic self-contained statements — rule-based clause splitting
- hybrid: HDT macro-structure + PROPOSITION micro-structure for mixed content

Atomic block = table, code block — không bao giờ cắt ngang.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from ragbot.shared.intrinsic_metrics import EkimetricsThresholds

# Module-level import — referenced by ``apply_cross_check`` (line ~520) and
# ``smart_chunk`` Layer-5 branch (line ~1908). Both were authored expecting a
# module-scope name but a matching top-level ``from ... import`` was missing,
# producing ``NameError: get_boot_config`` whenever those paths executed.
# Fix is mandatory for ``smart_chunk_atomic`` (Wave B1) which delegates the
# text-block run to legacy ``smart_chunk``.
from ragbot.shared.bootstrap_config import get_boot_config
from ragbot.shared.constants import (
    DEFAULT_ADAPCHUNK_L5_CONFIDENCE_THRESHOLD,
    DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED,
    DEFAULT_ADAPCHUNK_L5_HDT_MIN_HEADINGS,
    DEFAULT_ADAPCHUNK_L5_MIXED_CONTENT_WARN_THRESHOLD,
    DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_FALLBACK,
    DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_RULE,
    DEFAULT_ADAPCHUNK_L5_PROPOSITION_MAX_AVG_BLOCK_LEN,
    DEFAULT_ADAPCHUNK_L5_PROPOSITION_MAX_HEADINGS,
    DEFAULT_ADAPCHUNK_L5_SEMANTIC_MIN_AVG_BLOCK_LEN,
    DEFAULT_ATOMIC_OVERSIZE_WARN_MULTIPLIER,
    DEFAULT_CHILD_CHUNK_OVERLAP,
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_CHUNK_FINGERPRINT_CHARS,
    DEFAULT_CHUNK_MAX_SIZE,
    DEFAULT_CHUNK_MIN_CLAUSE_LEN,
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_OVERLAP_BOUNDARY,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED,
    DEFAULT_CSV_FORMAT_COMMA_RATIO,
    DEFAULT_CSV_FORMAT_SAMPLE_LINES,
    DEFAULT_CSV_FORMAT_SENTENCE_END_RATIO,
    DEFAULT_CSV_FORMAT_TABLE_RUN_MIN_LINES,
    DEFAULT_CSV_MIN_COMMAS,
    DOCPROFILE_CODE_FENCE_PATTERN,
    DOCPROFILE_CODE_FENCES_PER_BLOCK,
    DOCPROFILE_FORMULA_PATTERN,
    DOCPROFILE_IMAGE_PATTERN,
    DEFAULT_HDT_H2_MIN_COUNT,
    DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES,
    DEFAULT_HDT_HEADINGS_NORM,
    DEFAULT_HDT_LONG_DOC_WORDS,
    DEFAULT_HYBRID_HEADINGS_NORM,
    DEFAULT_HYBRID_LONG_DOC_WORDS,
    DEFAULT_HYBRID_MIXED_THRESHOLD,
    DEFAULT_PROPOSITION_FEW_HEADINGS_MAX,
    DEFAULT_PROPOSITION_LONG_DOC_WORDS,
    DEFAULT_PROPOSITION_THRESHOLD_WORDS,
    DEFAULT_RECURSIVE_MIXED_THRESHOLD,
    DEFAULT_RECURSIVE_SHORT_AVG_LEN,
    DEFAULT_RECURSIVE_TABLES_NORM,
    DEFAULT_SEMANTIC_AVG_LEN_NORM,
    DEFAULT_SEMANTIC_FEW_HEADINGS_MAX,
    DEFAULT_SEMANTIC_LONG_DOC_WORDS,
    DEFAULT_SEMANTIC_SIMILARITY_THRESHOLD,
    DEFAULT_STRATEGY_MIN_CONFIDENCE,
    DEFAULT_STRATEGY_WEIGHTS,
    DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED,
    DEFAULT_TABLE_CSV_FOOTER_CHUNK_SAMPLE_ROWS,
    DEFAULT_TABLE_CSV_HEADER_CHUNK_SAMPLE_ROWS,
    DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS,
    DEFAULT_TABLE_DUAL_GROUP_MAX_CHARS,
    DEFAULT_TABLE_STRATEGY,
    DEFAULT_TABLE_CSV_MIN_NON_EMPTY_CELLS,
    DEFAULT_TABLE_CSV_POST_MIN_CHARS,
    DEFAULT_TABLE_CSV_PRE_MIN_CHARS,
    DEFAULT_TABLE_FOOTER_MAX_CHARS,
    DEFAULT_TABLE_FOOTER_PRESERVE_ENABLED,
    DEFAULT_TOPIC_NUMBERED_MARKER_RE,
    DEFAULT_TOPIC_PARAGRAPH_MIN_CHARS,
)

logger = structlog.get_logger(__name__)

# Sub-modules extracted from the chunking god-file (strangler split). Re-exported
# so every existing `from ragbot.shared.chunking import X` keeps resolving.
# (_SENTENCE_END_CHARS now lives in analyze and arrives via the star-import.)
from ragbot.shared.chunking.vn_structural import *  # noqa: E402,F401,F403
from ragbot.shared.chunking.analyze import *  # noqa: E402,F401,F403
from ragbot.shared.chunking.blocks import *  # noqa: E402,F401,F403
from ragbot.shared.chunking.csv_chunker import *  # noqa: E402,F401,F403
from ragbot.shared.chunking.strategies import *  # noqa: E402,F401,F403
def generate_parent_child_chunks(
    text: str,
    parent_size: int = DEFAULT_CHUNK_SIZE,
    child_size: int = DEFAULT_CHILD_CHUNK_SIZE,
    child_overlap: int = DEFAULT_CHILD_CHUNK_OVERLAP,
) -> list[dict]:
    """Generate parent-child chunk hierarchy for small-to-big retrieval.

    Parents are large chunks (default 1024 chars) stored for LLM context.
    Children are small chunks (default 256 chars) embedded for precise retrieval.

    @param text: document content
    @param parent_size: max chars per parent chunk
    @param child_size: max chars per child chunk
    @param child_overlap: overlap between child chunks (chars)
    @return: flat list of dicts with keys: content, is_parent, parent_index, chunk_index
    """
    if not text or not text.strip():
        return []

    # Step 1: split into parent chunks. When the document has markdown headings
    # (native or promoted from VN admin/legal markers), OR plain-text VN legal
    # markers (Chương/Mục/Điều/Phần), use HDT so each parent carries its
    # "[Chapter > Section > Article]" structural path. Otherwise fall back to
    # recursive (table-aware) splitting. chunk_overlap=0 intentional: parents
    # must not overlap to avoid child duplication during embed.
    _lines = text.split("\n")
    _has_markdown_headings = any(
        line.lstrip().startswith(("# ", "## ", "### "))
        for line in _lines
    )
    _has_vn_markers = sum(
        1 for line in _lines if _VN_HEADING_DETECT_RE.match(line.strip())
    ) >= DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES
    if _has_markdown_headings or _has_vn_markers:
        # Promote plain-text markers in case the caller skipped the upstream
        # promote step (defensive — keeps this function self-contained).
        if _has_vn_markers and not _has_markdown_headings:
            text = promote_vn_hierarchical_headings(text)
        parent_chunks = _chunk_hdt(text, chunk_size=parent_size)
    else:
        parent_chunks = _chunk_recursive_with_tables(text, chunk_size=parent_size, chunk_overlap=0)
    if not parent_chunks:
        return []

    result: list[dict] = []
    global_index = 0

    for parent_idx, parent_content in enumerate(parent_chunks):
        # Add the parent entry
        result.append({
            "content": parent_content,
            "is_parent": True,
            "parent_index": parent_idx,
            "chunk_index": global_index,
        })
        parent_global_index = global_index
        global_index += 1

        # Step 2: split parent into child chunks
        if len(parent_content) <= child_size:
            # Parent is small enough to be its own child
            result.append({
                "content": parent_content,
                "is_parent": False,
                "parent_index": parent_idx,
                "chunk_index": global_index,
                "parent_global_index": parent_global_index,
            })
            global_index += 1
        elif any(bt == "table" for bt, _ in _split_into_blocks(parent_content)):
            # Atomic-block protection: a TABLE-type parent must NOT be cut by
            # the plain RecursiveCharacterTextSplitter — that strands the
            # header on the first child and leaves the rest as header-less
            # orphan rows. Route through the table-aware splitter which
            # re-prepends the header per row-group (shape-based table detect,
            # domain-neutral).
            child_texts = _chunk_recursive_with_tables(
                parent_content, child_size, child_overlap,
            )
            for child_text in child_texts:
                child_text = child_text.strip()
                if not child_text:
                    continue
                result.append({
                    "content": child_text,
                    "is_parent": False,
                    "parent_index": parent_idx,
                    "chunk_index": global_index,
                    "parent_global_index": parent_global_index,
                })
                global_index += 1
        else:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            child_splitter = RecursiveCharacterTextSplitter(
                chunk_size=child_size,
                chunk_overlap=child_overlap,
                separators=["\n\n", "\n", ". ", " ", ""],
            )
            child_texts = child_splitter.split_text(parent_content)
            for child_text in child_texts:
                child_text = child_text.strip()
                if not child_text:
                    continue
                result.append({
                    "content": child_text,
                    "is_parent": False,
                    "parent_index": parent_idx,
                    "chunk_index": global_index,
                    "parent_global_index": parent_global_index,
                })
                global_index += 1

    logger.debug(
        "parent_child_chunks_generated",
        parents=len(parent_chunks),
        total=len(result),
        children=len([r for r in result if not r["is_parent"]]),
    )

    return result


# ---------------------------------------------------------------------------
# Structural path extraction (HDT chunks)
# ---------------------------------------------------------------------------

# extract_structural_path + _STRUCTURAL_PATH_RE moved to strategies (used by
# _chunk_hybrid there); they arrive in this namespace via `from .strategies import *`.


def _emit_atomic_block(
    block_type: str,
    content: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Emit an atomic block as chunks without cutting across its boundary.

    Atomic block rule (per spec):
        * FORMULA / IMAGE — preserved whole regardless of size. Splitting
          a LaTeX expression or an image-caption pair destroys semantic
          atomicity (you can't half-cite a formula).
        * TABLE — preserved whole when ≤ oversize threshold; oversized
          tables split by row groups WITH the header re-prepended (the
          existing :func:`_chunk_recursive_with_tables` row-grouping
          logic, here driven from the single-table content path).
        * CODE — preserved whole. Splitting a fenced code block by char
          count produces syntactically broken fragments; downstream
          retrieval / generation treats partial code as low-trust.

    Oversized FORMULA / IMAGE / CODE emit a structured warning so ops
    can spot pathological inputs (e.g. a 10KB single ``$$…$$`` formula
    that signals a corrupted parse upstream).
    """
    threshold = int(chunk_size * DEFAULT_ATOMIC_OVERSIZE_WARN_MULTIPLIER)

    if block_type == "table":
        # Re-use the existing table-aware path which handles oversized
        # tables by header-preserving row groups.
        return _chunk_recursive_with_tables(content, chunk_size, chunk_overlap)

    # FORMULA / IMAGE / CODE — keep whole.
    if len(content) > threshold:
        logger.warning(
            "atomic_block_oversized_kept_whole",
            block_type=block_type,
            content_chars=len(content),
            threshold_chars=threshold,
        )
    return [content]


def _smart_chunk_with_atomic_protect(
    *,
    text: str,
    strategy: str,
    chunk_size: int,
    chunk_overlap: int,
    profile: dict,
) -> list[str]:
    """Run the selected strategy on text segments while preserving atomic blocks.

    Algorithm:
        1. Split the doc into typed blocks via
           :func:`_split_into_blocks_with_atomic` (text / table / formula /
           image / code).
        2. For each atomic block (``is_atomic_block_type`` True), emit it
           via :func:`_emit_atomic_block` — no strategy splitter runs on
           atomic content, so no cut can land mid-formula or mid-image.
        3. For text blocks, dispatch to the chosen strategy splitter
           (HDT / semantic / hybrid / proposition / recursive). Strategy
           selection still saw the FULL document upstream, so profile-
           based decisions remain undistorted by the partitioning here.
        4. ``table_csv`` strategy keeps its whole-document fast path —
           the doc is already row-aligned CSV so partitioning would
           strand the header.

    Telemetry (structlog ``formula_image_atomic_protect``):
        * atomic_block_count — total atomic blocks detected
        * cuts_avoided — proxy = atomic_block_count (each atomic block
          would have produced ≥ 1 cut under the flag-off splitter when
          its content exceeded chunk_size, AND any non-oversized atomic
          block could still have been re-split by a downstream strategy
          mismatch). Lower-bound estimate; safe for ops dashboards.
    """
    # table_csv is a whole-doc strategy — partitioning would lose the
    # shared header row across data-row chunks.
    if strategy == "table_csv":
        return _chunk_table_csv_with_context(
            text,
            header_footer_enabled=bool(
                get_boot_config(
                    "table_csv_emit_header_footer_chunks_enabled",
                    DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED,
                ),
            ),
        )

    blocks = _split_into_blocks_with_atomic(text)
    chunks: list[str] = []
    atomic_count = 0
    cuts_avoided = 0

    for block_type, content in blocks:
        if _is_atomic_block_type(block_type):
            atomic_count += 1
            # An atomic block whose content is longer than chunk_size
            # would have been cut N≥1 times by the flag-off strategy
            # splitter (RecursiveCharacterTextSplitter / sentence
            # boundary). Count those as avoided cuts.
            if len(content) > chunk_size:
                cuts_avoided += max(1, len(content) // chunk_size)
            chunks.extend(_emit_atomic_block(block_type, content, chunk_size, chunk_overlap))
        else:
            # Text block — dispatch to the chosen strategy splitter.
            if strategy == "hdt":
                chunks.extend(_chunk_hdt(content, chunk_size))
            elif strategy == "semantic":
                chunks.extend(_chunk_semantic(content, chunk_size, chunk_overlap))
            elif strategy == "hybrid":
                chunks.extend(_chunk_hybrid(content, chunk_size, chunk_overlap))
            elif strategy == "proposition":
                chunks.extend(_chunk_proposition(content, chunk_size, chunk_overlap))
            else:
                chunks.extend(
                    _chunk_recursive_with_tables(content, chunk_size, chunk_overlap),
                )

    logger.info(
        "formula_image_atomic_protect",
        feature_flag="formula_image_atomic_protect_enabled",
        strategy=strategy,
        block_count=len(blocks),
        atomic_block_count=atomic_count,
        cuts_avoided=cuts_avoided,
        table_count=profile.get("table_count", 0),
    )

    return [c.strip() for c in chunks if c.strip()]


_MD_HEADING_LINE_RE = re.compile(r"^#{1,6} .+$", re.MULTILINE)


def _prefix_section_headings(text: str, chunks: list[str]) -> list[str]:
    """Prepend each chunk's active markdown section heading when the splitter cut
    it off (Anthropic Contextual Retrieval + AdapChunk B3).

    A "## Dịch vụ triệt lông" title and the table under it can land in different
    chunks after a size-based split, stranding the table with no service context.
    For every chunk we locate its position in the source, find the nearest
    preceding ``##`` heading, and prepend it when the chunk does not already start
    with / contain it — so each chunk is self-describing for BOTH embedding and
    stats extraction. Domain-neutral; no-op without markdown headings.
    """
    headings = [(m.start(), m.group(0).strip()) for m in _MD_HEADING_LINE_RE.finditer(text)]
    if not headings:
        return chunks
    out: list[str] = []
    search_from = 0
    for chunk in chunks:
        c = chunk.strip()
        fp = c.split("\n", 1)[0][:60] if c else ""
        pos = text.find(fp, search_from) if fp else -1
        if pos < 0 and fp:
            pos = text.find(fp)
        if pos >= 0:
            search_from = pos + 1
        active: str | None = None
        for hpos, htext in headings:
            if pos < 0 or hpos <= pos:
                active = htext
            else:
                break
        if active and not c.startswith("#") and active not in chunk:
            out.append(f"{active}\n{chunk}")
        else:
            out.append(chunk)
    return out


def smart_chunk(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str | None = None,
    with_metadata: bool = False,
    *,
    table_strategy: str = DEFAULT_TABLE_STRATEGY,
) -> list:  # list[str] when with_metadata=False, list[dict] when True
    """Dynamic chunking — auto-detect strategy nếu không chỉ định.

    @param text: document content
    @param chunk_size: max chars per chunk
    @param chunk_overlap: overlap giữa chunks
    @param strategy: force strategy (None = auto-detect)
    @param with_metadata: when True, return list[dict] with content +
        metadata (parent_headings stack from H1/H2). Default False keeps
        the legacy list[str] contract for the dozens of existing callers.
    @param table_strategy: CSV/table fast-path strategy resolved from the
        chunking policy chain (``table_csv`` row-as-chunk vs
        ``table_dual_index``). Only consulted on the auto-detect path
        (``strategy is None``); an explicit ``strategy`` is honoured as-is.
    @return: list of chunk strings (default) or list of {content, metadata}
        dicts (when ``with_metadata=True``)
    """
    if not text or not text.strip():
        return []

    # Promote plain-text "Chương/Mục/Điều" markers → markdown headings so the
    # HDT detector can recognise VN admin/legal hierarchy. No-op when fewer
    # than DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES markers are present.
    text = promote_vn_hierarchical_headings(text)

    # Auto-detect strategy if not specified
    if strategy is None:
        profile = analyze_document(text)
        strategy, confidence = select_strategy(profile, table_strategy=table_strategy)
        logger.info(
            "chunking_strategy_selected",
            strategy=strategy,
            confidence=confidence,
            profile=profile,
        )

        # AdapChunk Layer 5 — Rule Cross-check. Feature flag gated, default
        # OFF. When enabled, runs 5 priority-ordered override rules to fix
        # known selector blindspots; logs an audit event for every override
        # so operators can tune weights/thresholds in ``system_config``.
        if bool(
            get_boot_config(
                "adapchunk_layer5_cross_check_enabled",
                DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED,
            )
        ):
            original_strategy, original_confidence = strategy, confidence
            strategy, confidence, override_reason = apply_cross_check(
                strategy, confidence, profile,
            )
            if override_reason is not None:
                logger.info(
                    "adapchunk_l5_strategy_overridden",
                    step_name="adapchunk_l5_crosscheck",
                    feature_flag="adapchunk_layer5_cross_check_enabled",
                    original_strategy=original_strategy,
                    original_confidence=original_confidence,
                    override_strategy=strategy,
                    override_confidence=confidence,
                    override_reason=override_reason,
                )
    else:
        profile = analyze_document(text)
        confidence = 1.0

    # FORMULA / IMAGE / CODE atomic-block protection. When enabled,
    # partition the doc and route atomic blocks AROUND every strategy
    # splitter so formulas / images / code fences never get cut
    # mid-block. Text-only segments still flow through the selected
    # strategy unchanged.
    if _atomic_protect_enabled():
        atomic_chunks = _smart_chunk_with_atomic_protect(
            text=text,
            strategy=strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            profile=profile,
        )
        chunks = atomic_chunks
    # T1 — table_csv FAST PATH. When the doc is column-aligned
    # CSV, row-as-chunk runs unconditionally (no per-block detour) so each
    # row stays atomic with its header.
    elif strategy == "table_csv":
        chunks = _chunk_table_csv_with_context(
            text,
            header_footer_enabled=bool(
                get_boot_config(
                    "table_csv_emit_header_footer_chunks_enabled",
                    DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED,
                ),
            ),
        )
    # table_dual_index — row chunks PLUS whole-table group chunk(s) so
    # aggregation / "list-all" queries retrieve every row at once.
    elif strategy == "table_dual_index":
        chunks = _chunk_table_dual_index(text)
    # Table isolation: tables ALWAYS use recursive (table-aware)
    # regardless of overall strategy chosen. HDT is the ONE exception —
    # splitting by block tears the heading hierarchy across calls so the
    # second [Chương > Mục > Điều] block restarts with empty path stack,
    # losing structural context. HDT keeps full-document scope.
    elif profile["table_count"] > 0 and strategy not in ("recursive", "hdt"):
        blocks = _split_into_blocks(text)
        chunks: list[str] = []
        for block_type, content in blocks:
            if block_type == "table":
                chunks.extend(_chunk_recursive_with_tables(content, chunk_size, chunk_overlap))
            else:
                if strategy == "semantic":
                    chunks.extend(_chunk_semantic(content, chunk_size, chunk_overlap))
                elif strategy == "hybrid":
                    chunks.extend(_chunk_hybrid(content, chunk_size, chunk_overlap))
                elif strategy == "proposition":
                    chunks.extend(_chunk_proposition(content, chunk_size, chunk_overlap))
                else:
                    chunks.extend(_chunk_recursive_with_tables(content, chunk_size, chunk_overlap))
    elif strategy == "hdt":
        chunks = _chunk_hdt(text, chunk_size)
    elif strategy == "semantic":
        chunks = _chunk_semantic(text, chunk_size, chunk_overlap)
    elif strategy == "hybrid":
        chunks = _chunk_hybrid(text, chunk_size, chunk_overlap)
    elif strategy == "proposition":
        chunks = _chunk_proposition(text, chunk_size, chunk_overlap)
    else:
        chunks = _chunk_recursive_with_tables(text, chunk_size, chunk_overlap)

    # Re-attach each section's markdown heading to any chunk the splitter severed
    # it from, so every chunk is self-describing (Anthropic Contextual Retrieval +
    # AdapChunk B3): a "## Dịch vụ triệt lông" table stays linked to its service so
    # both the embedding AND the stats extractor can bind the row to its section.
    # No-op when the doc carries no markdown headings.
    if strategy != "hdt" and "#" in text:
        chunks = _prefix_section_headings(text, chunks)

    logger.debug(
        "smart_chunk_result",
        strategy=strategy,
        confidence=confidence,
        chunks=len(chunks),
    )

    if with_metadata:
        # Build the H1/H2 offset index ONCE per source text; per-chunk
        # lookup becomes O(log N) via bisect. Previous code re-scanned
        # the full document with two regexes per chunk — O(K·M) for K
        # chunks and document length M.
        heading_index = _HeadingIndex(text)
        out: list[dict] = []
        for chunk in chunks:
            # HDT-prefixed chunks carry their structural path inline
            # (``[H1 > H2]\n``). Prefer that signal — the fingerprint
            # search the index does fails on such chunks because the
            # prefix is synthetic. Non-HDT chunks (recursive / semantic)
            # fall back to the offset-index lookup.
            parsed = extract_structural_path(chunk)
            sp = parsed.get("structural_path")
            if sp and sp.get("parts"):
                parents = list(sp["parts"])
            else:
                parents = heading_index.parents_for_chunk(chunk)
            out.append({
                "content": chunk,
                "metadata": {
                    "parent_headings": parents,
                    "strategy": strategy,
                },
            })
        return out
    return chunks


# ---------------------------------------------------------------------------
# Orphan merge — post-process after smart_chunk
# ---------------------------------------------------------------------------


def merge_orphan_chunks(
    chunks: list[str],
    *,
    orphan_threshold: int = DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    max_size: int = DEFAULT_CHUNK_MAX_SIZE,
) -> list[str]:
    """Merge chunks shorter than ``orphan_threshold`` into the next non-orphan.

    Headers and isolated bullets carry no fact value alone — their
    embedding becomes noise. Merging keeps section header adjacent to its
    content for embed-signal coherence. Skipped when the merged chunk
    would exceed ``max_size`` to preserve embedding granularity.
    """
    if not chunks:
        return []
    out: list[str] = []
    pending: list[str] = []
    for chunk in chunks:
        if len(chunk) < orphan_threshold:
            pending.append(chunk)
            continue
        if pending:
            merged = "\n".join(pending + [chunk])
            if len(merged) <= max_size:
                out.append(merged)
            else:
                out.extend(pending)
                out.append(chunk)
            pending = []
        else:
            out.append(chunk)
    if pending:
        # Trailing orphans — fold into previous non-orphan when capacity allows.
        if out:
            tail = "\n".join([out[-1]] + pending)
            if len(tail) <= max_size:
                out[-1] = tail
                return out
        out.extend(pending)
    return out


# ---------------------------------------------------------------------------
# AdapChunk Layer 6 — atomic-aware chunking: list[Block] -> list[Chunk]
# ---------------------------------------------------------------------------
#
# Parallel surface to legacy ``smart_chunk(text: str) -> list[str|dict]``.
# Legacy entry point is KEPT for the 100+ callers that flatten parsed blocks
# back to a single string and lose the ``is_atomic`` flag at the boundary.
# This new surface preserves the Block stream from the parser and emits
# atomic blocks (TABLE / FORMULA / IMAGE / CODE) as standalone Chunks,
# applying the dispatched strategy only to runs of TEXT-like blocks.
#
# Per debug doc luannt-debug-rag-git.md §6.3 + §18.2 Tầng 6.


def smart_chunk_atomic(
    blocks: "list[Block]",
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str | None = None,
    with_metadata: bool = True,
    record_tenant_id: "TenantId | None" = None,
    record_bot_id: "BotId | None" = None,
    document_id: "DocumentId | None" = None,
    embedding_model_version: "EmbeddingModelVersion | None" = None,
    corpus_version: "CorpusVersion | None" = None,
    ingested_at: "datetime | None" = None,
) -> "list[Chunk]":
    """AdapChunk-compliant chunking — receives ``list[Block]``, preserves atomic flag.

    INVARIANT: TABLE / FORMULA / IMAGE / CODE blocks with ``is_atomic=True``
    are NEVER cut. The dispatched strategy applies only to runs of non-atomic
    (TEXT-like) blocks; atomic blocks are emitted as standalone chunks with
    ``context_before`` and ``context_after`` concatenated around the content.

    Parallel to legacy ``smart_chunk(text: str) -> list[str]`` which receives
    flattened text and loses the atomic flag — kept intact for backward-compat
    with the existing call sites (Wave B2 will wire ``smart_chunk_atomic`` into
    ``document_service`` while legacy callers migrate incrementally).

    Per AdapChunk spec Layer 6: chunk signature is ``list[Block] -> list[Chunk]``.

    @param blocks: parser output — heterogeneous block stream
    @param chunk_size: max chars per chunk for TEXT-block runs
    @param chunk_overlap: overlap between TEXT-block chunks
    @param strategy: force strategy for TEXT runs (None = auto-detect)
    @param with_metadata: API parity with ``smart_chunk``; Chunk already carries
        metadata via its dataclass so this flag is currently a no-op (preserved
        for caller symmetry during the Wave B1→B2 migration window)
    @param record_tenant_id: tenant UUID populated on every emitted Chunk
    @param record_bot_id: bot UUID populated on every emitted Chunk
    @param document_id: source-document UUID populated on every emitted Chunk
    @param embedding_model_version: model version string carried into Chunk
    @param corpus_version: corpus version int carried into Chunk
    @param ingested_at: ingestion timestamp carried into Chunk
    @return: list of ``Chunk`` entities (atomic blocks + chunked text runs)
    """
    # Localized imports — avoid top-of-module domain coupling so the existing
    # legacy ``smart_chunk`` code path stays untouched and import-order safe.
    from datetime import datetime as _datetime, timezone as _timezone
    from uuid import uuid4

    from ragbot.domain.entities.document import Block, Chunk
    from ragbot.shared.constants import DEFAULT_ATOMIC_BLOCK_TYPES
    from ragbot.shared.types import (
        BotId,
        ChunkingStrategyName,
        CorpusVersion,
        DocumentId,
        EmbeddingModelVersion,
        TenantId,
    )

    if not blocks:
        return []

    # Wave B1 scaffold defaults: when caller has not yet plumbed identity
    # (typical during early reorg integration), fill sentinel UUIDs so the
    # Chunk dataclass invariants hold. Wave B2 wires real values through
    # ``document_service`` ingest path and these defaults disappear.
    tenant_id = record_tenant_id if record_tenant_id is not None else TenantId(uuid4())
    bot_id = record_bot_id if record_bot_id is not None else BotId(uuid4())
    doc_id = document_id if document_id is not None else DocumentId(uuid4())
    embed_ver = (
        embedding_model_version
        if embedding_model_version is not None
        else EmbeddingModelVersion("")
    )
    corpus_ver = corpus_version if corpus_version is not None else CorpusVersion(0)
    ts = ingested_at if ingested_at is not None else _datetime.now(_timezone.utc)

    # Strategy name carried onto every emitted Chunk. ChunkingStrategyName
    # uses the uppercase Literal vocabulary {HDT, SEMANTIC, PROPOSITION,
    # HYBRID}; the legacy ``smart_chunk`` accepts lowercase + "recursive".
    # Default to HYBRID when caller does not constrain — it is the most
    # general entry in the Literal and matches the mixed-stream emit pattern
    # of this function.
    def _normalize_strategy(s: str | None) -> ChunkingStrategyName:
        if s is None:
            return "HYBRID"
        up = s.upper()
        if up in {"HDT", "SEMANTIC", "PROPOSITION", "HYBRID"}:
            return up  # type: ignore[return-value]
        return "HYBRID"

    strategy_used = _normalize_strategy(strategy)

    chunks: list[Chunk] = []
    text_buffer: list[Block] = []

    for block in blocks:
        # An ``is_atomic`` flag explicitly set on the parser output wins
        # over the type membership check — parsers may decide a particular
        # TABLE is small enough to inline, or a CODE fence should be cut.
        is_atomic = bool(block.is_atomic) or block.type in DEFAULT_ATOMIC_BLOCK_TYPES
        if is_atomic:
            if text_buffer:
                chunks.extend(
                    _chunk_text_blocks_to_chunks(
                        text_buffer,
                        strategy=strategy,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        strategy_used=strategy_used,
                        record_tenant_id=tenant_id,
                        record_bot_id=bot_id,
                        document_id=doc_id,
                        embedding_model_version=embed_ver,
                        corpus_version=corpus_ver,
                        ingested_at=ts,
                    )
                )
                text_buffer = []
            chunks.append(
                _block_to_atomic_chunk(
                    block,
                    strategy_used=strategy_used,
                    record_tenant_id=tenant_id,
                    record_bot_id=bot_id,
                    document_id=doc_id,
                    embedding_model_version=embed_ver,
                    corpus_version=corpus_ver,
                    ingested_at=ts,
                )
            )
        else:
            text_buffer.append(block)

    if text_buffer:
        chunks.extend(
            _chunk_text_blocks_to_chunks(
                text_buffer,
                strategy=strategy,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                strategy_used=strategy_used,
                record_tenant_id=tenant_id,
                record_bot_id=bot_id,
                document_id=doc_id,
                embedding_model_version=embed_ver,
                corpus_version=corpus_ver,
                ingested_at=ts,
            )
        )

    logger.debug(
        "smart_chunk_atomic_result",
        strategy=strategy_used,
        blocks_in=len(blocks),
        chunks_out=len(chunks),
    )
    return chunks


def _chunk_text_blocks_to_chunks(
    text_blocks: "list[Block]",
    *,
    strategy: str | None,
    chunk_size: int,
    chunk_overlap: int,
    strategy_used: "ChunkingStrategyName",
    record_tenant_id: "TenantId",
    record_bot_id: "BotId",
    document_id: "DocumentId",
    embedding_model_version: "EmbeddingModelVersion",
    corpus_version: "CorpusVersion",
    ingested_at: "datetime",
) -> "list[Chunk]":
    """Join contiguous TEXT-like blocks, dispatch legacy ``smart_chunk``,
    wrap each text shard into a ``Chunk`` entity preserving the block-type
    provenance of the constituent source blocks.
    """
    from ragbot.domain.entities.document import Chunk
    from ragbot.shared.hashing import content_hash_required
    from ragbot.shared.types import ChunkId
    from uuid import uuid4

    if not text_blocks:
        return []
    joined = "\n\n".join(b.content for b in text_blocks if b.content)
    if not joined.strip():
        return []
    # Reuse legacy ``smart_chunk`` string-based dispatch for the TEXT span.
    text_shards = smart_chunk(
        joined,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strategy=strategy,
        with_metadata=False,
    )
    block_types = tuple({b.type for b in text_blocks})
    pages = [b.page_number for b in text_blocks if b.page_number is not None]
    page_number = pages[0] if pages else None

    out: list[Chunk] = []
    for shard in text_shards:
        content = shard if isinstance(shard, str) else shard.get("content", "")
        if not content:
            continue
        out.append(
            Chunk(
                id=ChunkId(uuid4()),
                document_id=document_id,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                strategy_used=strategy_used,
                block_types=block_types,
                narrated_text=content,
                contextual_prefix="",
                original_content=content,
                structural_path=None,
                page_number=page_number,
                content_hash=content_hash_required(content),
                embedding_model_version=embedding_model_version,
                corpus_version=corpus_version,
                ingested_at=ingested_at,
            )
        )
    return out


def _block_to_atomic_chunk(
    block: "Block",
    *,
    strategy_used: "ChunkingStrategyName",
    record_tenant_id: "TenantId",
    record_bot_id: "BotId",
    document_id: "DocumentId",
    embedding_model_version: "EmbeddingModelVersion",
    corpus_version: "CorpusVersion",
    ingested_at: "datetime",
) -> "Chunk":
    """Wrap an atomic Block into a Chunk with ``context_before`` /
    ``context_after`` concatenated around the block content.

    ``narrated_text`` defaults to the same surrounded text — Wave E (the
    type-specific narrator step) will overwrite this with an LLM-generated
    description for TABLE / FORMULA / IMAGE blocks where appropriate.
    """
    from ragbot.domain.entities.document import Chunk
    from ragbot.shared.hashing import content_hash_required
    from ragbot.shared.types import ChunkId
    from uuid import uuid4

    parts: list[str] = []
    if block.context_before:
        parts.append(block.context_before)
    parts.append(block.content)
    if block.context_after:
        parts.append(block.context_after)
    full = "\n\n".join(p for p in parts if p)
    return Chunk(
        id=ChunkId(uuid4()),
        document_id=document_id,
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
        strategy_used=strategy_used,
        block_types=(block.type,),
        narrated_text=full,
        contextual_prefix="",
        original_content=block.content,
        structural_path=None,
        page_number=block.page_number,
        content_hash=content_hash_required(block.content),
        embedding_model_version=embedding_model_version,
        corpus_version=corpus_version,
        ingested_at=ingested_at,
        metadata={
            "is_atomic": True,
            "block_type": block.type,
            "ocr_metadata": dict(block.ocr_metadata) if block.ocr_metadata else {},
        },
    )
