"""Ingest stage methods (U2-U7 + finalize) split out of the ``ingest()`` god-method.

Behaviour-preserving decomposition (pure relocation): ``ingest()`` (in
``ingest_core``) builds an ``_IngestCtx`` holding all state that crosses phase
boundaries, then calls these stage methods in order. Each stage mutates ``ctx``
in place. Logic, log event names, SQL, exception types, ordering, and the
``_phase_d_step`` wrapping are identical to the original single method.

``session_with_tenant`` / ``_bulk_insert_chunks`` are referenced via the
``ingest_core`` module object (``_core.session_with_tenant``) so the existing
``monkeypatch.setattr(ds_mod.ingest_core, ...)`` test seams keep working.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from sqlalchemy import text

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.shared.chunking import (
    _count_topic_signals,
    _is_csv_format,
    _split_into_blocks_with_atomic,
    analyze_document,
    extract_structural_path,
    generate_parent_child_chunks,
    merge_orphan_chunks,
    promote_vn_hierarchical_headings,
    select_strategy,
    smart_chunk,
)
from ragbot.shared.chunking.tenant_style import apply_tenant_style
from ragbot.shared.constants import (
    DEFAULT_TABLE_STRATEGY,
    DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED,
    DEFAULT_ADAPCHUNK_LAYER3_DOC_PROFILE_ENABLED,
    DEFAULT_CHILD_CHUNK_OVERLAP,
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_CHUNK_MAX_SIZE,
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    DEFAULT_CLEANBASE_QUALITY_THRESHOLD,
    DEFAULT_CONTENT_TYPE_DISPATCH_ENABLED,
    DEFAULT_CLEANBASE_TIER0_ENABLED,
    DEFAULT_DOC_PROFILE_ANALYZER_PROVIDER,
    DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED,
    DEFAULT_CR_CONTEXT_MAX_TOKENS,
    DEFAULT_CR_CACHE_WARM_MIN_CHUNKS,
    DEFAULT_CR_ENHANCED_ENABLED,
    DEFAULT_CR_MAX_DOC_CHARS,
    DEFAULT_CR_PROMPT_CACHE_ENABLED,
    DEFAULT_DIFF_REINGEST_ENABLED,
    DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
    DEFAULT_NARRATE_TIMEOUT_S,
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_EMBEDDING_PASSAGE_PREFIX,
    DEFAULT_ENRICH_ROW_GATE_ENABLED,
    DEFAULT_ENRICHED_PREFIX_PERSIST,
    EMBEDDING_TEXT_STRATEGY_AUTO,
    STRUCTURAL_CHUNK_STRATEGIES,
    DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED,
    DEFAULT_ENRICHMENT_MAX_CONCURRENCY,
    DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS,
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_LANGUAGE,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_METADATA_EXTRACTION_MODEL,
    DEFAULT_METADATA_EXTRACTION_MODEL as _DEFAULT_CR_FALLBACK_MODEL,
    DEFAULT_PARENT_CHUNK_SIZE,
    DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_HEAD,
    DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_TAIL,
    DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED,
    DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S,
    DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS,
    NARRATE_METADATA_KEY_BLOCK_TYPE,
    NARRATE_METADATA_KEY_NARRATED_TEXT,
    NARRATE_METADATA_KEY_RAW_CHUNK,
    VI_DOMAIN_LANGUAGES,
    WHOLE_DOC_THRESHOLD_CHARS,
)
from ragbot.infrastructure.doc_profile.registry import build_doc_profile_analyzer
from ragbot.shared.vi_tokenizer import segment_vi_compounds
from ragbot.application.services.content_type_router import (
    emit_type_histogram,
    group_by_block_type,
)
from ragbot.application.services.structured_ref_extractor import (
    extract_structured_refs,
)
from ragbot.infrastructure.embedding_text.registry import (
    build_embedding_text_strategy,
)
from ragbot.application.services.contextual_chunk_enrichment import (
    emit_chunk_quality_event,
    enrich_chunk_with_context,
    score_chunk_quality,
)
from ragbot.shared.bot_limits import resolve_bot_limit
from ragbot.shared.contextual_enrichment import enrich_chunks
from ragbot.shared.errors import ExternalServiceError
from ragbot.shared.ingestion_validator import validate_ingestion

from ragbot.application.services.document_service import ingest_core as _core
from ragbot.application.services.document_service.ingest_phases import (
    IngestResult,
    _phase_d_step,
    _update_doc_progress,
)
from ragbot.application.services.document_service.text_processing import (
    _clean_document_text,
    chunk_type_for,
    should_skip_row_enrich,
)
from ragbot.application.services.narrate_dispatch import (
    narrate_chunks_for_embed as _narrate_chunks_for_embed,
)

logger = structlog.get_logger(__name__)


@dataclass
class _IngestCtx:
    """All state that crosses ingest phase boundaries. Stages mutate in place."""

    # ── method params (set at ingest() entry) ──
    record_bot_id: uuid.UUID
    title: str
    content: str
    source_url: str
    source_type: str
    language: str
    mime_type: str
    existing_doc_id: uuid.UUID | None
    record_tenant_id: uuid.UUID | None
    workspace_id: str | None
    channel_type: str
    raw_bytes: bytes | None
    file_name: str | None
    blocks: list[Any] | None
    step_tracker: Any | None

    # ── audit / timing (set in ingest() preamble) ──
    audit: Any = None
    audit_bot: str = ""
    ingest_t0: float = 0.0

    # ── derived doc identity ──
    doc_id: uuid.UUID = field(default_factory=uuid.uuid4)
    is_reindex: bool = False
    content_hash: str = ""

    # ── U2 parse → U4 ──
    parser_row_chunks: list[dict] | None = None

    # ── U3 clean → U7 ──
    extracted_metadata: dict = field(default_factory=dict)

    # ── U4 chunk → U5 / U7 ──
    chunks: list[str] = field(default_factory=list)
    parent_child_enabled: bool = False
    pc_hierarchy: list[dict] = field(default_factory=list)
    chunking_strategy: str = "recursive"
    chunking_confidence: float = 1.0
    # Reconciled strategy actually used after all U4 overrides
    # (whole_document / parent_child win over the auto-selected name).
    # This is the value surfaced to IngestResult + the DocumentIngested event.
    strategy_used: str = "recursive"
    is_whole_document: bool = False
    topic_signals: int = 0
    orphans_merged_count: int = 0
    whole_doc_threshold: int = 0
    max_topic_signals: int = 0

    # ── U5 enrich → U6 / U7 ──
    enriched_chunks: list[str] = field(default_factory=list)
    cr_raw_chunks: list[str] = field(default_factory=list)
    chunk_contexts: list[str] = field(default_factory=list)
    cr_active: bool = False
    cr_model: str = ""
    enrich_enabled: bool = False
    u6_precomputed: list[str | None] | None = None
    quality_scores: list[float] = field(default_factory=list)
    chunk_quality_skip_indices: set[int] = field(default_factory=set)
    vi_seg_enabled: bool = False
    vi_seg_timeout: int = 0
    vi_seg_lang_eligible: bool = False
    effective_language: str = ""
    skip_row_enrich: bool = False
    enriched_persist_enabled: bool = False
    persist_chunks: list[str] = field(default_factory=list)

    # ── U6 → U7 ──
    segmented_chunks: list[str | None] = field(default_factory=list)

    # ── U7 embed/store ──
    new_chunk_hashes: list[str] = field(default_factory=list)
    existing_hashes: dict[int, str] = field(default_factory=dict)
    chunks_to_embed: list[tuple[int, str, str]] = field(default_factory=list)
    unchanged_indices: list[int] = field(default_factory=list)
    stale_indices: list[int] = field(default_factory=list)
    changed_indices: list[int] = field(default_factory=list)
    pc_parent_indices: set[int] = field(default_factory=set)
    new_embeddings: dict[int, Any] = field(default_factory=dict)
    narrate_meta_by_idx: dict[int, dict[str, Any]] = field(default_factory=dict)
    spec: Any = None
    rows: list[dict] = field(default_factory=list)
    late_chunking_enabled: bool = False
    use_sliding: bool = False
    any_embedded: bool = False

    # ── finalize ──
    final_state: str = ""
    flip_committed: bool = False


class _StageChunkMixin:
    """Ingest U3 clean / U4 chunk / U6 vn-segment stages."""

    async def _stage_u3_clean(self, ctx: _IngestCtx) -> None:
        content = ctx.content
        title = ctx.title
        step_tracker = ctx.step_tracker
        # ── U3 ingest_clean — CleanBase Tier-0 + legacy cleaner ──
        # Phase D observability. Two-stage chain:
        #   (a) CleanBase Tier-0 sanitize (T1-Safety): HTML strip + NFC +
        #       zero-width remove + prompt-inject blacklist. Opt-out via
        #       ``system_config.cleanbase_tier0_enabled``.
        #   (b) Legacy ``_clean_document_text`` (hyphenation fix, repeated
        #       header strip, whitespace collapse). Still runs the
        #       ``PROMPT_INJECTION_PATTERNS`` sweep for defence-in-depth;
        #       second pass is idempotent because Tier-0 already substituted
        #       the redaction token (``[REDACTED]`` never re-matches an
        #       injection pattern).
        async with _phase_d_step(step_tracker, "ingest_clean") as _u3_ctx:
            _u3_n_chars_in = len(content)
            if self._cfg is not None:
                cleanbase_tier0_enabled = bool(await self._cfg.get(
                    "cleanbase_tier0_enabled",
                    DEFAULT_CLEANBASE_TIER0_ENABLED,
                ))
                cleaning_enabled = bool(await self._cfg.get(
                    "ingestion_cleaning_enabled", True,
                ))
            else:
                cleanbase_tier0_enabled = DEFAULT_CLEANBASE_TIER0_ENABLED
                cleaning_enabled = True

            sanitize_report = None
            # Production bug 2026-05-18: ``_sanitizer`` is not always
            # initialised in ``__init__`` (DI wiring may skip it). Direct
            # attribute access raised AttributeError on 4/4 ingest_clean
            # rows. ``getattr`` degrades a missing attribute to the same
            # "unwired" path as an explicit None — preserves backward-compat
            # with the wired DI path while protecting the ingest pipeline.
            _sanitizer = getattr(self, "_sanitizer", None)
            if cleanbase_tier0_enabled and _sanitizer is not None:
                content, sanitize_report = _sanitizer.sanitize(content)
                logger.info(
                    "cleanbase_tier0_scrub",
                    step_name="cleanbase_tier0_scrub",
                    feature_flag="cleanbase_tier0_enabled",
                    flag_value=cleanbase_tier0_enabled,
                    provider=sanitize_report.provider_name,
                    n_chars_in=sanitize_report.n_chars_in,
                    n_chars_out=sanitize_report.n_chars_out,
                    html_tags_stripped=sanitize_report.html_tags_stripped,
                    zero_width_removed=sanitize_report.zero_width_removed,
                    injection_patterns_matched=(
                        sanitize_report.injection_patterns_matched
                    ),
                    nfc_changed=sanitize_report.nfc_changed,
                    total_redactions=sanitize_report.total_redactions,
                )
            else:
                logger.debug(
                    "cleanbase_tier0_skipped",
                    step_name="cleanbase_tier0_scrub",
                    feature_flag="cleanbase_tier0_enabled",
                    flag_value=cleanbase_tier0_enabled,
                    reason=(
                        "flag_off" if not cleanbase_tier0_enabled
                        else "no_sanitizer_wired"
                    ),
                )

            if cleaning_enabled:
                content = _clean_document_text(content)
                logger.debug(
                    "ingestion_cleaning_applied",
                    title=title,
                    char_count=len(content),
                )
            metadata_for_step: dict[str, Any] = {
                "cleaning_enabled": cleaning_enabled,
                "cleanbase_tier0_enabled": cleanbase_tier0_enabled,
                "n_chars_in": _u3_n_chars_in,
                "n_chars_out": len(content),
                "n_chars_stripped": max(0, _u3_n_chars_in - len(content)),
            }
            if sanitize_report is not None:
                metadata_for_step.update({
                    "tier0_provider": sanitize_report.provider_name,
                    "tier0_html_tags_stripped": (
                        sanitize_report.html_tags_stripped
                    ),
                    "tier0_zero_width_removed": (
                        sanitize_report.zero_width_removed
                    ),
                    "tier0_injection_patterns_matched": (
                        sanitize_report.injection_patterns_matched
                    ),
                    "tier0_nfc_changed": sanitize_report.nfc_changed,
                    "tier0_total_redactions": (
                        sanitize_report.total_redactions
                    ),
                })
            _u3_ctx.set_metadata(**metadata_for_step)

        # LLM-based metadata extraction.
        extracted_metadata: dict = {}
        if self._cfg is not None:
            metadata_extraction_enabled = bool(await self._cfg.get("metadata_extraction_enabled", False))
        else:
            metadata_extraction_enabled = False
        if metadata_extraction_enabled:
            extracted_metadata = await self._extract_metadata_llm(content, title)
            if extracted_metadata:
                logger.info(
                    "metadata_extraction_ok",
                    title=title,
                    doc_type=extracted_metadata.get("document_type"),
                    topics=extracted_metadata.get("key_topics"),
                )
        ctx.content = content
        ctx.extracted_metadata = extracted_metadata

    async def _stage_u4_chunk(self, ctx: _IngestCtx) -> None:
        content = ctx.content
        title = ctx.title
        language = ctx.language
        step_tracker = ctx.step_tracker
        parser_row_chunks = ctx.parser_row_chunks
        doc_id = ctx.doc_id
        record_bot_id = ctx.record_bot_id
        record_tenant_id = ctx.record_tenant_id
        _audit = ctx.audit
        _audit_bot = ctx.audit_bot
        # ── U4 ingest_chunk — checkpoint start. Phase D records the row at
        # the trailing boundary (line: chunking_strategy_selected audit) so
        # the existing branched logic stays untouched. ``duration_ms`` is
        # captured manually + reported via metadata; the StepTracker's own
        # ``duration_ms`` will be ~0 (body is the metadata setter).
        _u4_t0 = time.perf_counter()
        # ── Whole-document optimisation (Task 3.3) ──
        # Small documents are stored as a single chunk to preserve full context.
        if self._cfg is not None:
            whole_doc_enabled = bool(await self._cfg.get("whole_doc_enabled", True))
            whole_doc_threshold = await self._cfg.get_int("whole_doc_threshold_chars", WHOLE_DOC_THRESHOLD_CHARS)
        else:
            whole_doc_enabled = False
            whole_doc_threshold = WHOLE_DOC_THRESHOLD_CHARS

        # Whole-doc strategy: store entire doc as single chunk to preserve
        # full context. Reject when content is CSV-like (table_csv per-row
        # wins) OR when the doc has ≥ N distinct topical signals (one chunk
        # per topic — avoids embedding-centroid dilution across topics).
        if self._cfg is not None:
            max_topic_signals = await self._cfg.get_int(
                "whole_doc_max_topic_signals",
                DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS,
            )
        else:
            max_topic_signals = DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS
        topic_signals = _count_topic_signals(content)
        is_whole_document = (
            whole_doc_enabled
            and len(content) < whole_doc_threshold
            and not _is_csv_format(content)
            and topic_signals <= max_topic_signals
        )

        # ── Parent-child chunking config ──
        parent_child_enabled = False
        pc_hierarchy: list[dict] = []
        _chunking_strategy: str = "recursive"
        _chunking_confidence: float = 1.0
        _orphans_merged_count: int = 0

        # Phase A — resolve the config-driven chunking policy (table strategy +
        # optional owner-forced strategy) from the per-bot → platform → constant
        # chain. Default is behaviour-neutral (table_csv, no force).
        _policy = await self._resolve_chunking_policy(
            record_bot_id, record_tenant_id=record_tenant_id,
        )
        _table_strategy: str = _policy.get("table_strategy", DEFAULT_TABLE_STRATEGY)
        _force_strategy: str | None = _policy.get("force_strategy")

        # P3 Tenant-Profiling — normalize the owner's non-standard styling
        # (uppercase-as-heading / owner column separator) into canonical
        # markdown BEFORE block-detection + chunking, so the global rules in
        # ``analyze``/``smart_chunk`` work unchanged. Default OFF → identity
        # no-op (existing bots byte-identical). Reads per-bot config only — no
        # per-bot branching in core.
        _style = _policy.get("style_profile") or {}
        if _style.get("heading_uppercase_promote") or _style.get("table_separator"):
            _content_before = content
            content = apply_tenant_style(
                content,
                heading_uppercase_promote=bool(_style.get("heading_uppercase_promote")),
                table_separator=str(_style.get("table_separator") or ""),
            )
            if content != _content_before:
                logger.info(
                    "tenant_style_applied",
                    record_bot_id=str(record_bot_id),
                    uppercase_promote=bool(_style.get("heading_uppercase_promote")),
                    table_separator=str(_style.get("table_separator") or ""),
                    chars_before=len(_content_before),
                    chars_after=len(content),
                )

        if self._cfg is not None:
            parent_child_enabled = bool(await self._cfg.get("parent_child_enabled", False))

        if is_whole_document:
            chunks = [content]
            parent_child_enabled = False  # whole-doc overrides parent-child
            logger.info(
                "whole_document_single_chunk",
                title=title,
                char_count=len(content),
                threshold=whole_doc_threshold,
            )
        elif parent_child_enabled:
            # Parent-child chunking (small-to-big retrieval)
            if self._cfg is not None:
                pc_parent_size = await self._cfg.get_int("parent_chunk_size", DEFAULT_PARENT_CHUNK_SIZE)
                pc_child_size = await self._cfg.get_int("child_chunk_size", DEFAULT_CHILD_CHUNK_SIZE)
                pc_child_overlap = await self._cfg.get_int("child_chunk_overlap", DEFAULT_CHILD_CHUNK_OVERLAP)
            else:
                pc_parent_size = DEFAULT_PARENT_CHUNK_SIZE
                pc_child_size = DEFAULT_CHILD_CHUNK_SIZE
                pc_child_overlap = DEFAULT_CHILD_CHUNK_OVERLAP

            # Promote VN admin/legal hierarchy so parent splitter (HDT path) can
            # preserve "[Chương > Mục > Điều]" context. No-op when the markers
            # are absent.
            content = promote_vn_hierarchical_headings(content)

            pc_hierarchy = generate_parent_child_chunks(
                content,
                parent_size=pc_parent_size,
                child_size=pc_child_size,
                child_overlap=pc_child_overlap,
            )
            if not pc_hierarchy:
                # Fallback to flat chunking
                parent_child_enabled = False
                chunks = [content]
            else:
                chunks = [item["content"] for item in pc_hierarchy]
                logger.info(
                    "parent_child_chunking",
                    title=title,
                    parents=len([h for h in pc_hierarchy if h["is_parent"]]),
                    children=len([h for h in pc_hierarchy if not h["is_parent"]]),
                )
        else:
            # Chunk (table-aware) — primary: system_config, fallback: settings
            if self._cfg is not None:
                chunk_size = await self._cfg.get_int("rag_default_chunk_size", self._settings.rag.default_chunk_size)
                chunk_overlap = await self._cfg.get_int("rag_default_chunk_overlap", self._settings.rag.default_chunk_overlap)
            else:
                chunk_size = self._settings.rag.default_chunk_size
                chunk_overlap = self._settings.rag.default_chunk_overlap

            # ─── AdapChunk reorg 2026-05-14 Wave B2 — Block pipeline gate ───
            # Goal: route ingestion through the Block-aware pipeline
            # (Layer 2 → 3 → 4 → 5 → 6) so atomic-block invariants
            # (FORMULA / IMAGE / CODE / TABLE) survive end-to-end instead of
            # being flattened into prose and re-segmented by the text-only
            # ``smart_chunk`` path.
            #
            # Dependencies (must merge before flipping the flag default ON):
            #   * Wave B1 (atomic-chunking signature) — adds Block-aware
            #     ``smart_chunk`` overloads. Until merged we still call the
            #     existing text-API; ``smart_chunk`` already runs internal
            #     atomic protection via ``_smart_chunk_with_atomic_protect``
            #     when ``formula_image_atomic_protect_enabled`` is ON, so
            #     no regression while we wait.
            #   * Wave D1 (Layer-3 refinement) — adds
            #     ``analyze_document_blocks`` returning the 10-feature
            #     ``DocumentProfile`` entity. Until merged we ``getattr``
            #     onto the legacy ``analyze_document(content)`` dict so the
            #     selector keeps its existing contract.
            #
            # Honors Phần 6.9 gap #1 (atomic-block propagation) and gap #2
            # (Layer-2 context buffer wiring) from the AdapChunk reorg plan.
            # Default OFF — flipping requires both upstream waves to land.
            block_pipeline_enabled = (
                await self._cfg.get_bool(
                    "adapchunk_block_pipeline_enabled",
                    DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED,
                )
                if self._cfg is not None
                else DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED
            )
            # Ekimetrics 5-metric selector flag (LREC 2026, arXiv 2603.25333) —
            # resolved once for both the block-pipeline + legacy paths. Default
            # OFF preserves the weighted scorer. When ON, select_strategy runs
            # the intrinsic-metric selector for AMBIGUOUS PROSE docs only (it
            # sits AFTER the CSV→table + legal→HDT structural fast-paths, so
            # those bots are unaffected).
            ekimetrics_enabled = (
                await self._cfg.get_bool(
                    "ekimetrics_5metric_selector_enabled", False,
                )
                if self._cfg is not None
                else False
            )

            if block_pipeline_enabled:
                # ── NEW AdapChunk-compliant Block pipeline ──
                # Layer 2: attach 1-2 sentence context buffer to atomic
                # blocks. When the upstream parser surfaces a ``blocks``
                # list (Wave B1+), we feed it directly; the flag-gated
                # implementation no-ops on empty input today.
                from ragbot.shared.chunking import apply_cross_check
                from ragbot.shared.context_buffer import attach_context_buffer

                # The upstream parser threads its typed ``Block`` list onto
                # ``ctx.blocks`` (ingest_core builds it from the ``blocks``
                # param). Feed it directly so the Layer-2 buffer runs on the
                # real atomic blocks. ``None`` / empty (direct-text API,
                # parsers that emit no blocks) → empty list, the buffer call
                # is skipped and the text-flatten fallback below stays the path.
                parsed_blocks: list = list(ctx.blocks or [])
                if parsed_blocks:
                    parsed_blocks = attach_context_buffer(parsed_blocks)

                # Layer 3: analyze document profile. Prefer the Wave-D1
                # block-aware analyzer; fall back to the legacy
                # text-flatten analyzer so CI does not break before D1.
                content = promote_vn_hierarchical_headings(content)
                _analyze_blocks = getattr(
                    __import__(
                        "ragbot.shared.chunking", fromlist=["*"]
                    ),
                    "analyze_document_blocks",
                    None,
                )
                if _analyze_blocks is not None and parsed_blocks:
                    _doc_profile = _analyze_blocks(parsed_blocks)
                else:
                    _doc_profile = analyze_document(content)

                # Layer 4: rule-based strategy selection. The CSV/table
                # fast-path returns the policy-resolved ``_table_strategy``
                # (table_csv vs table_dual_index).
                _chunking_strategy, _chunking_confidence = select_strategy(
                    _doc_profile, table_strategy=_table_strategy,
                    ekimetrics_enabled=ekimetrics_enabled, text=content,
                )

                # Layer 5: cross-check overrides. ``apply_cross_check``
                # is a pure function returning
                # ``(strategy, confidence, override_reason)``. The
                # ``smart_chunk`` callee re-runs it internally when the
                # L5 flag is ON, so we mirror that here to keep the
                # strategy we record in metadata in sync with what the
                # chunker will actually use.
                _chunking_strategy, _chunking_confidence, _override_reason = (
                    apply_cross_check(
                        _chunking_strategy,
                        _chunking_confidence,
                        _doc_profile,
                    )
                )
                if _override_reason is not None:
                    logger.info(
                        "adapchunk_b2_block_pipeline_override",
                        step_name="adapchunk_b2_block_pipeline",
                        feature_flag="adapchunk_block_pipeline_enabled",
                        override_reason=_override_reason,
                        strategy=_chunking_strategy,
                        confidence=_chunking_confidence,
                    )
            else:
                # ── DEPRECATED 2026-05-14 AdapChunk-reorg Wave B2 ──
                # Legacy text-flatten path. Kept verbatim as the
                # default branch while ``adapchunk_block_pipeline_enabled``
                # is OFF so operators can roll back instantly by flipping
                # the flag. DO NOT delete — Block pipeline is opt-in
                # until Wave B1 / D1 ship and have soaked in load tests.
                #
                # Promote VN admin/legal hierarchy ("Chương/Mục/Điều")
                # into markdown headings so HDT detector can score this
                # doc. No-op for documents without the marker pattern.
                # Must run BEFORE analyze_document so total_headings
                # reflects promoted markers.
                content = promote_vn_hierarchical_headings(content)

                # Compute chunking strategy confidence for metadata
                _doc_profile = analyze_document(content)
                _chunking_strategy, _chunking_confidence = select_strategy(
                    _doc_profile, table_strategy=_table_strategy,
                    ekimetrics_enabled=ekimetrics_enabled, text=content,
                )

            # Phase A — owner/operator forced strategy wins over auto-detect
            # (e.g. a legal-bot owner pinning ``hdt`` for every upload). Only
            # applied when the resolved policy carries a valid force value.
            if _force_strategy:
                _chunking_strategy = _force_strategy
                _chunking_confidence = 1.0

            # AdapChunk Layer 3 DocumentProfile refine.
            # Owner-opt-in via system_config flag. When enabled, we compute
            # the 10-feature ``DocumentProfile`` entity (rule-based, no LLM)
            # and log it for downstream A/B comparison. Default OFF preserves
            # the dict-only path; the entity is NOT yet wired into
            # select_strategy (the Ekimetrics 5-metric selector takes the
            # entity as input). This wiring ships the plumbing + telemetry
            # only.
            adapchunk_l3_enabled = (
                await self._cfg.get_bool(
                    "adapchunk_layer3_doc_profile_enabled",
                    DEFAULT_ADAPCHUNK_LAYER3_DOC_PROFILE_ENABLED,
                )
                if self._cfg is not None
                else DEFAULT_ADAPCHUNK_LAYER3_DOC_PROFILE_ENABLED
            )
            if adapchunk_l3_enabled:
                analyzer_provider = (
                    await self._cfg.get(
                        "doc_profile_analyzer_provider",
                        DEFAULT_DOC_PROFILE_ANALYZER_PROVIDER,
                    )
                    if self._cfg is not None
                    else DEFAULT_DOC_PROFILE_ANALYZER_PROVIDER
                )
                _profile_start = time.monotonic()
                try:
                    analyzer = build_doc_profile_analyzer(str(analyzer_provider))
                    profile_entity = analyzer.analyze(content)
                    _profile_duration_ms = int(
                        (time.monotonic() - _profile_start) * 1000
                    )
                    logger.info(
                        "adapchunk_layer3_profile",
                        step_name="adapchunk_l3_profile",
                        feature_flag="adapchunk_layer3_doc_profile_enabled",
                        flag_value=True,
                        provider=str(analyzer_provider),
                        duration_ms=_profile_duration_ms,
                        total_blocks=profile_entity.total_blocks,
                        total_words=profile_entity.total_words,
                        heading_total=profile_entity.heading_counts.total,
                        table_count=profile_entity.table_count,
                        table_avg_rows=profile_entity.table_avg_rows,
                        formula_count=profile_entity.formula_count,
                        image_count=profile_entity.image_count,
                        code_block_count=profile_entity.code_block_count,
                        heading_ratio=profile_entity.heading_ratio,
                        mixed_content_score=profile_entity.mixed_content_score,
                        detected_language=profile_entity.detected_language,
                        has_toc=profile_entity.has_toc,
                    )
                except (ValueError, RuntimeError) as exc:
                    # Graceful degradation — refine is an enhancement, never
                    # a hard dependency. Surface as warning so operators can
                    # spot a misconfigured provider, but keep ingest moving.
                    logger.warning(
                        "adapchunk_layer3_profile_failed",
                        step_name="adapchunk_l3_profile",
                        feature_flag="adapchunk_layer3_doc_profile_enabled",
                        flag_value=True,
                        provider=str(analyzer_provider),
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
            else:
                logger.debug(
                    "adapchunk_layer3_profile_skipped",
                    step_name="adapchunk_l3_profile",
                    feature_flag="adapchunk_layer3_doc_profile_enabled",
                    flag_value=False,
                    reason="flag_off",
                )

            # G2 fix (Stream A Phase 2): when the upstream parser already emitted
            # row-shaped chunks (Excel, Google Sheets CSV via the registry),
            # bypass smart_chunk re-chunking — re-chunking would flatten the
            # rows into prose and lose 1-row-per-chunk semantics, which is the
            # root cause of the V13 over-refuse cluster on factoid queries.
            #
            # Scope gate (2026-05-26): the preserve path is ONLY safe when
            # the parser intent is row-per-chunk (excel, google_sheets). For
            # markdown / plain-text parsers, "1 chunk = whole document"
            # bypassed smart_chunk and produced a single 74KB chunk for a
            # 98KB legal corpus, which broke retrieval recall. Detect the
            # parser via metadata stamp and re-route to smart_chunk when
            # the chunk is markdown/text.
            _row_preserve_providers = {"excel_openpyxl", "google_sheets"}
            _parser_is_row_shaped = False
            if parser_row_chunks:
                _first_meta = parser_row_chunks[0].get("metadata") or {}
                _parser_tag = str(_first_meta.get("parser") or "").strip()
                _parser_is_row_shaped = _parser_tag in _row_preserve_providers
            if parser_row_chunks and _parser_is_row_shaped:
                raw_chunks = [
                    c["content"] for c in parser_row_chunks if c.get("content")
                ]
                _chunking_strategy = "parser_preserve"
                _chunking_confidence = 1.0
            else:
                raw_chunks = smart_chunk(
                    content,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    strategy=_chunking_strategy,  # U3-1: pass explicit to avoid re-analysis
                )
            if not raw_chunks:
                chunks = [content]
            else:
                if self._cfg is not None:
                    orphan_threshold = await self._cfg.get_int(
                        "chunk_orphan_threshold", DEFAULT_CHUNK_ORPHAN_THRESHOLD
                    )
                    chunk_max_size = await self._cfg.get_int(
                        "chunk_max_size", DEFAULT_CHUNK_MAX_SIZE
                    )
                else:
                    orphan_threshold = DEFAULT_CHUNK_ORPHAN_THRESHOLD
                    chunk_max_size = DEFAULT_CHUNK_MAX_SIZE
                # table_csv / table_dual_index / parser_preserve emit one
                # atomic row per chunk (dual_index also adds whole-table group
                # chunks). Orphan-merge would fold short rows (< orphan_threshold
                # chars) back into a multi-row blob, re-introducing the cross-row
                # price conflate bug. Keep row-atomic strategies intact.
                if _chunking_strategy in (
                    "table_csv", "table_dual_index", "parser_preserve",
                ):
                    chunks = list(raw_chunks)
                    _orphans_merged_count = 0
                else:
                    chunks = merge_orphan_chunks(
                        raw_chunks,
                        orphan_threshold=orphan_threshold,
                        max_size=chunk_max_size,
                    )
                    _orphans_merged_count = len(raw_chunks) - len(chunks)

        # ── U4 ingest_chunk — record Phase D row at chunking boundary ──
        # Trailing-record pattern: chunking branches above bleed into many
        # variables (whole_document, parent_child, smart_chunk + orphans);
        # re-indenting all 110 lines into a single async-with would be
        # high-risk for a pure-observability change. Metadata reports the
        # captured ``duration_ms_actual`` so analyzers see real time spent.
        _u4_dur_ms = int((time.perf_counter() - _u4_t0) * 1000)
        # M25 — block-modality histogram. RAG-Anything mindset: a doc's
        # block-type mix (table-heavy vs prose vs mixed) is the strongest
        # signal for tuning rerank gates, chunk sizing, and per-modality
        # retrieval. Compute it once on the raw content using the same
        # atomic splitter the chunker uses so the audit row reflects what
        # downstream nodes will see. Read-only (no I/O), bounded by doc
        # size; safe to keep inline on the hot path.
        from collections import Counter as _Counter
        try:
            _blocks_for_stats = _split_into_blocks_with_atomic(content)
            _block_type_histogram: dict[str, int] = dict(
                _Counter(btype for btype, _body in _blocks_for_stats),
            )
        except (ValueError, TypeError, AttributeError):
            # Observability MUST NOT break ingest — narrow types cover the
            # realistic splitter failure modes (malformed text, regex /
            # encoding edge case). Anything broader would mask a real bug.
            logger.warning("m25_block_histogram_failed", exc_info=True)
            _block_type_histogram = {}
        logger.info(
            "ingest_blocks_by_type",
            blocks_by_type=_block_type_histogram,
            n_blocks_total=sum(_block_type_histogram.values()),
            doc_id=str(doc_id),
            record_bot_id=str(record_bot_id),
        )
        async with _phase_d_step(step_tracker, "ingest_chunk") as _u4_ctx:
            if is_whole_document:
                _u4_strategy_used = "whole_document"
            elif parent_child_enabled and pc_hierarchy:
                _u4_strategy_used = "parent_child"
            else:
                _u4_strategy_used = _chunking_strategy
            _u4_avg = (
                sum(len(c) for c in chunks) // len(chunks) if chunks else 0
            )
            _u4_ctx.set_metadata(
                strategy_used=_u4_strategy_used,
                n_chunks_out=len(chunks),
                chunk_size_avg=_u4_avg,
                language=language,
                topic_signals=topic_signals,
                orphans_merged_count=_orphans_merged_count,
                duration_ms_actual=_u4_dur_ms,
                # M25 — block-modality histogram persists on request_steps
                # so analytics can correlate retrieval quality vs source
                # block mix without re-parsing the document.
                blocks_by_type=_block_type_histogram,
            )

            # P0-2 lossless-coverage (OBSERVE-only): flag source numbers that no
            # chunk carries — a silently-dropped value (price/row) is a number-HALLU
            # even while Faithfulness reads 1.0 ("honest but blind"). Deterministic,
            # no LLM, currency/language-neutral, NEVER raises (pure observability).
            from ragbot.shared.number_format import find_dropped_numbers  # noqa: PLC0415 — local keeps hot-path import lean
            _dropped_nums = find_dropped_numbers(ctx.content, chunks)
            if _dropped_nums:
                _u4_ctx.set_metadata(numbers_dropped=len(_dropped_nums))
                logger.warning(
                    "chunk_numeric_coverage_gap",
                    doc_id=str(doc_id),
                    record_bot_id=str(record_bot_id),
                    strategy_used=_u4_strategy_used,
                    dropped_count=len(_dropped_nums),
                    sample=_dropped_nums[:5],
                )

        # Progress checkpoint after U4 chunk: 20% done, chunks_total known.
        await _update_doc_progress(
            self._sf, record_tenant_id, doc_id,
            current_step="enriching", progress_percent=20,
            chunks_total=len(chunks), chunks_processed=0,
        )

        # Audit: which chunking branch we ended up in (after all overrides).
        if _audit is not None:
            if is_whole_document:
                _strategy_name = "whole_document"
                _why = f"len<{whole_doc_threshold}"
            elif parent_child_enabled and pc_hierarchy:
                _strategy_name = "parent_child"
                _why = "parent_child_enabled"
            else:
                _strategy_name = _chunking_strategy
                _why = f"smart_chunk confidence={_chunking_confidence:.2f}"
            await _audit.log(
                _audit_bot,
                "ingest",
                "chunking_strategy_selected",
                {
                    "strategy": _strategy_name,
                    "why": _why,
                    "total_raw_chars": len(content),
                    "n_chunks": len(chunks),
                    "topic_signals": topic_signals,
                    "max_topic_signals": max_topic_signals,
                    "orphans_merged_count": _orphans_merged_count,
                },
            )
        ctx.content = content
        ctx.chunks = chunks
        ctx.parent_child_enabled = parent_child_enabled
        ctx.pc_hierarchy = pc_hierarchy
        ctx.chunking_strategy = _chunking_strategy
        # Surface the reconciled strategy (post whole_document / parent_child
        # override) as record-of-truth for IngestResult + DocumentIngested.
        ctx.strategy_used = _u4_strategy_used
        ctx.chunking_confidence = _chunking_confidence
        ctx.is_whole_document = is_whole_document
        ctx.topic_signals = topic_signals
        ctx.orphans_merged_count = _orphans_merged_count
        ctx.whole_doc_threshold = whole_doc_threshold
        ctx.max_topic_signals = max_topic_signals

    async def _stage_u6_vn_segment(self, ctx: _IngestCtx) -> None:
        chunks = ctx.chunks
        title = ctx.title
        step_tracker = ctx.step_tracker
        _u6_precomputed = ctx.u6_precomputed
        _vi_seg_lang_eligible = ctx.vi_seg_lang_eligible
        vi_seg_enabled = ctx.vi_seg_enabled
        vi_seg_timeout = ctx.vi_seg_timeout
        _effective_language = ctx.effective_language
        persist_chunks = ctx.persist_chunks
        # ── U6 ingest_vn_segment — VN compound segmentation phase wrap ──
        # Phase D observability: pure metadata; logic untouched. When the
        # gate is closed (non-VI bot or feature off), the wrap STILL fires
        # with ``skipped=True`` so analytics see the row count = ingest count.
        #
        # U5 ∥ U6 fast path: when CR was active, _u6_precomputed already holds
        # the segmentation results computed concurrently with CR enrich above
        # (each chunk ran _enrich_one + _vn_seg_one via asyncio.gather). In
        # that case we skip the second gather pass entirely — wall-time saved
        # equals the full segment_vi_compounds call duration (typically 50-
        # 500ms for a 50-100 chunk doc at underthesea speed).
        #
        # Non-CR path (legacy enrich): _u6_precomputed is None; we fall back
        # to the original gather + to_thread pass on persist_chunks (enriched
        # text) so BM25 still sees compound-split enriched content.
        async with _phase_d_step(step_tracker, "ingest_vn_segment") as _u6_ctx:
            _segmented_chunks: list[str | None] = [None] * len(chunks)
            _u6_seg_changed = 0
            if _u6_precomputed is not None:
                # CR path: results pre-computed concurrently with U5.
                _segmented_chunks = list(_u6_precomputed)
                _u6_seg_changed = sum(1 for s in _segmented_chunks if s is not None)
                if vi_seg_enabled and _vi_seg_lang_eligible and _u6_seg_changed > 0:
                    logger.info(
                        "vi_compound_segmentation_applied",
                        title=title,
                        chunks_total=len(chunks),
                        chunks_segmented=_u6_seg_changed,
                        parallel=True,
                        mode="u5_u6_concurrent",
                    )
            elif vi_seg_enabled and _vi_seg_lang_eligible:
                # Non-CR path: run VN segment on persist_chunks (enriched text)
                # so content_segmented matches the enriched content column.
                # Dispatch in parallel — order preserved by gather returning
                # results in argument order.
                _seg_results: list[str] = await asyncio.gather(
                    *(
                        asyncio.to_thread(
                            segment_vi_compounds, _txt, timeout_s=vi_seg_timeout,
                        )
                        for _txt in persist_chunks
                    ),
                )
                # Only persist when segmentation actually changed the text —
                # avoids storing identical copies for English / already-tokenised
                # content (saves disk + keeps reads simple via COALESCE).
                for _i, (_txt, _segmented) in enumerate(zip(persist_chunks, _seg_results)):
                    if _segmented != _txt:
                        _segmented_chunks[_i] = _segmented
                        _u6_seg_changed += 1
                logger.info(
                    "vi_compound_segmentation_applied",
                    title=title,
                    chunks_total=len(chunks),
                    chunks_segmented=_u6_seg_changed,
                    parallel=True,
                    mode="sequential_after_enrich",
                )
            _u6_ctx.set_metadata(
                vi_seg_enabled=vi_seg_enabled,
                vi_seg_lang_eligible=_vi_seg_lang_eligible,
                language=_effective_language,
                n_chunks_total=len(chunks),
                n_chunks_segmented=_u6_seg_changed,
                skipped=not (vi_seg_enabled and _vi_seg_lang_eligible),
                parallel=True,
                u5_u6_concurrent=(_u6_precomputed is not None),
            )
        ctx.segmented_chunks = _segmented_chunks
