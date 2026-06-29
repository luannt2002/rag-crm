"""Ingest U7 embed+store stage — split from ingest_stages to keep files navigable."""
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
    DEFAULT_EMPTY_EMBED_FALLBACK_TEXT,
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


from ragbot.application.services.document_service.ingest_stages import _IngestCtx


class _StageStoreMixin:
    async def _stage_u7_embed_store(self, ctx: _IngestCtx) -> None:
        session_with_tenant = _core.session_with_tenant
        _bulk_insert_chunks = _core._bulk_insert_chunks
        content = ctx.content
        title = ctx.title
        step_tracker = ctx.step_tracker
        record_bot_id = ctx.record_bot_id
        record_tenant_id = ctx.record_tenant_id
        doc_id = ctx.doc_id
        is_reindex = ctx.is_reindex
        is_whole_document = ctx.is_whole_document
        chunks = ctx.chunks
        chunks_to_embed = ctx.chunks_to_embed
        stale_indices = ctx.stale_indices
        existing_hashes = ctx.existing_hashes
        new_chunk_hashes = ctx.new_chunk_hashes
        _pc_parent_indices = ctx.pc_parent_indices
        parent_child_enabled = ctx.parent_child_enabled
        pc_hierarchy = ctx.pc_hierarchy
        _chunking_strategy = ctx.chunking_strategy
        _chunking_confidence = ctx.chunking_confidence
        enriched_chunks = ctx.enriched_chunks
        enriched_persist_enabled = ctx.enriched_persist_enabled
        persist_chunks = ctx.persist_chunks
        _segmented_chunks = ctx.segmented_chunks
        _quality_scores = ctx.quality_scores
        chunk_contexts = ctx.chunk_contexts
        cr_active = ctx.cr_active
        cr_raw_chunks = ctx.cr_raw_chunks
        extracted_metadata = ctx.extracted_metadata
        _audit = ctx.audit
        _audit_bot = ctx.audit_bot
        # ── U7 ingest_embed_store — checkpoint start (embed + bulk-insert).
        _u7_t0 = time.perf_counter()
        # Embed only changed/new chunks (skip parents in parent-child mode)
        spec = await self._embedding_spec(
            record_bot_id=record_bot_id,
            record_tenant_id=record_tenant_id,
            language=ctx.effective_language or None,
        )
        new_embeddings: dict[int, Any] = {}  # {chunk_index: embedding}

        # Filter out parent chunks from embedding — parents don't need vectors
        _chunks_needing_embed = [
            (i, txt, h) for i, txt, h in chunks_to_embed
            if i not in _pc_parent_indices
        ]
        # Narrate dispatch metadata (Wave E3) — populated inside the embed
        # block below when the service is wired. Pre-declared so the
        # INSERT loops can always read ``.get(chunk_idx)`` even on the
        # "no chunks to embed" path (re-index of unchanged document).
        _narrate_meta_by_idx: dict[int, dict[str, Any]] = {}

        if _chunks_needing_embed:
            # Embedding-text strategy: choose what bytes the dense encoder
            # sees. Default ``prefix_plus_raw`` reproduces the historical
            # behaviour (``enriched_chunks[i]``). ``raw_only`` strips the
            # LLM-generated prefix to fix short-keyword dilution (e.g.
            # "Điều 3?" was losing to chunks whose prefix said "Đoạn 3 ...").
            # ``cr_raw_chunks`` snapshots ``chunks`` pre-enrichment at
            # ingest entry, so it's the canonical raw source.
            embed_text_strategy_name = await self._resolve_embedding_text_strategy_name(
                record_bot_id=record_bot_id,
                record_tenant_id=record_tenant_id,
            )
            # "auto" → derive from the document's CHUNK STRUCTURE, never from
            # bot identity (domain-neutral). Structural docs (HDT legal/admin
            # with Điều/Chương anchors) embed raw_only so the CR prefix does
            # not dilute exact-anchor lookup; prose/table/FAQ embed
            # prefix_plus_raw so situated context aids semantic match.
            if embed_text_strategy_name == EMBEDDING_TEXT_STRATEGY_AUTO:
                embed_text_strategy_name = (
                    "raw_only"
                    if _chunking_strategy in STRUCTURAL_CHUNK_STRATEGIES
                    else "prefix_plus_raw"
                )
            embed_text_strategy = build_embedding_text_strategy(
                embed_text_strategy_name,
            )

            def _embed_text_for(idx: int) -> str:
                raw_at_idx = (
                    cr_raw_chunks[idx]
                    if idx < len(cr_raw_chunks)
                    else enriched_chunks[idx]
                )
                enriched_at_idx = enriched_chunks[idx]
                # Recover the LLM prefix from the enriched chunk when the
                # ingest path persisted "{prefix}\n\n{raw}". If the strings
                # are identical (CR skipped, parent chunk, etc.) the prefix
                # is empty and the strategy degrades to raw.
                inferred_prefix = ""
                if enriched_at_idx != raw_at_idx and enriched_at_idx.endswith(raw_at_idx):
                    inferred_prefix = enriched_at_idx[: -len(raw_at_idx)].rstrip()
                return embed_text_strategy.build(
                    raw_chunk=raw_at_idx,
                    enriched_prefix=inferred_prefix or None,
                )

            texts_to_embed = [
                _embed_text_for(i) for i, _, _ in _chunks_needing_embed
            ]
            logger.info(
                "embedding_text_strategy_applied",
                strategy=embed_text_strategy.name,
                n_chunks=len(texts_to_embed),
            )

            # AdapChunk Tầng 6 — Narrate-then-Embed (Wave E3 wire fix).
            # Before this call, ``texts_to_embed`` contains raw chunk bytes
            # which for TABLE/FORMULA/IMAGE chunks would produce semantic
            # vectors disconnected from natural-language queries. We route
            # each chunk through the narrator strategy which:
            #   * TEXT — passthrough (no LLM cost)
            #   * FORMULA — LaTeX → "lực bằng khối lượng nhân gia tốc"
            #   * TABLE — pipe table → linearized sentences
            #   * IMAGE — OCR description
            # ``narrate_service is None`` → identity passthrough so the
            # legacy embed-target bytes are preserved. Per-chunk metadata
            # is captured into ``_narrate_meta_by_idx`` keyed by the
            # ABSOLUTE chunk index so the persist loops below can record
            # raw_chunk + narrated_text + block_type into metadata_json
            # for offline analysis.
            if self._narrate_service is not None:
                try:
                    # Hard timeout (CRIT audit 2026-06-13): a stalled narrate LLM
                    # call must not hang the worker on this document forever. On
                    # timeout fall back to the raw embed-target text (the same
                    # identity passthrough as the narrate_service-off path).
                    async with asyncio.timeout(DEFAULT_NARRATE_TIMEOUT_S):
                        narrated_texts, narrate_meta_list = (
                            await _narrate_chunks_for_embed(
                                texts_to_embed,
                                narrate_service=self._narrate_service,
                            )
                        )
                    # Map back to absolute chunk index for the persist loops.
                    for local_idx, meta in enumerate(narrate_meta_list):
                        if meta is None:
                            continue
                        abs_idx = _chunks_needing_embed[local_idx][0]
                        _narrate_meta_by_idx[abs_idx] = meta
                    texts_to_embed = narrated_texts
                    logger.info(
                        "narrate_then_embed_applied",
                        n_chunks=len(texts_to_embed),
                        n_meta_populated=sum(
                            1 for m in narrate_meta_list if m is not None
                        ),
                    )
                except TimeoutError:
                    logger.error(
                        "narrate_timeout_fallback_raw_embed",
                        timeout_s=DEFAULT_NARRATE_TIMEOUT_S,
                        n_chunks=len(texts_to_embed),
                        document_id=str(doc_id),
                    )
                    # texts_to_embed keeps its raw value → embedding proceeds.

            # Asymmetric embedding: prepend passage prefix.
            # Resolution: bot.plan_limits → system_config → DEFAULT (empty).
            # Re-ingest required after toggling.
            passage_prefix = await self._resolve_embedding_passage_prefix(
                record_bot_id=record_bot_id,
                record_tenant_id=record_tenant_id,
            )
            if passage_prefix:
                texts_to_embed = [f"{passage_prefix}{t}" for t in texts_to_embed]

            # Empty-input guard (embedder 422 protection). A few chunking
            # strategies (table_dual_index group/divider rows) linearise to
            # whitespace; Jina v3 rejects empty/whitespace-only inputs and
            # aborts the WHOLE document. Substitute a neutral placeholder so the
            # batch is accepted — the vector carries no signal and never matches
            # a real query. Logged for observability (which docs emit empties).
            _n_empty_embed = sum(1 for _t in texts_to_embed if not _t.strip())
            if _n_empty_embed:
                texts_to_embed = [
                    _t if _t.strip() else DEFAULT_EMPTY_EMBED_FALLBACK_TEXT
                    for _t in texts_to_embed
                ]
                logger.warning(
                    "embed_empty_input_substituted",
                    n_substituted=_n_empty_embed,
                    n_total=len(texts_to_embed),
                    document_id=str(doc_id),
                )

            # Late chunking: context-aware embedding (Jina-style approximation)
            late_chunking_enabled = False
            late_ctx_chars = 200
            # Sliding-window variant for long docs that blow past the
            # embedder's single-pass context window. Default OFF — gate via
            # ``system_config.late_chunking_sliding_enabled``.
            late_sliding_enabled = False
            late_sliding_window_chars = 0
            late_sliding_overlap_chars = 0
            late_sliding_threshold_chars = 0
            if self._cfg is not None:
                late_chunking_enabled = bool(await self._cfg.get("late_chunking_enabled", True))
                late_ctx_chars = await self._cfg.get_int("late_chunking_context_chars", 200)
                from ragbot.shared.constants import (
                    DEFAULT_LATE_CHUNKING_LONG_DOC_THRESHOLD_CHARS,
                    DEFAULT_LATE_CHUNKING_OVERLAP_CHARS,
                    DEFAULT_LATE_CHUNKING_SLIDING_ENABLED,
                    DEFAULT_LATE_CHUNKING_WINDOW_CHARS,
                )
                late_sliding_enabled = await self._cfg.get_bool(
                    "late_chunking_sliding_enabled",
                    DEFAULT_LATE_CHUNKING_SLIDING_ENABLED,
                )
                late_sliding_window_chars = await self._cfg.get_int(
                    "late_chunking_window_chars",
                    DEFAULT_LATE_CHUNKING_WINDOW_CHARS,
                )
                late_sliding_overlap_chars = await self._cfg.get_int(
                    "late_chunking_overlap_chars",
                    DEFAULT_LATE_CHUNKING_OVERLAP_CHARS,
                )
                late_sliding_threshold_chars = await self._cfg.get_int(
                    "late_chunking_long_doc_threshold_chars",
                    DEFAULT_LATE_CHUNKING_LONG_DOC_THRESHOLD_CHARS,
                )

            embed_results = None
            # Sliding path takes precedence when the flag is ON and the
            # document is long enough to actually benefit from per-window
            # local context (short docs reuse the single-prefix path).
            doc_chars = len(content or "")
            use_sliding = (
                late_sliding_enabled
                and doc_chars > late_sliding_threshold_chars
            )
            if use_sliding:
                try:
                    from ragbot.shared.late_chunking import late_chunk_embed_sliding
                    embed_results = await late_chunk_embed_sliding(
                        chunks=texts_to_embed,
                        document_text=content,
                        embedder=self._embedder,
                        window_chars=late_sliding_window_chars,
                        overlap_chars=late_sliding_overlap_chars,
                        context_prefix_chars=late_ctx_chars,
                        embed_kwargs={"spec": spec, "record_tenant_id": None},
                    )
                    logger.info(
                        "late_chunking_sliding_applied",
                        step_name="late_chunking_sliding",
                        feature_flag="late_chunking_sliding_enabled",
                        title=title,
                        doc_chars=doc_chars,
                        chunks=len(texts_to_embed),
                        window_chars=late_sliding_window_chars,
                        overlap_chars=late_sliding_overlap_chars,
                    )
                except (ValueError, TypeError, RuntimeError, ExternalServiceError) as exc:
                    logger.warning(
                        "late_chunking_sliding_failed_fallback",
                        feature_flag="late_chunking_sliding_enabled",
                        doc=title,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    embed_results = None

            if embed_results is None and late_chunking_enabled:
                try:
                    from ragbot.shared.late_chunking import late_chunk_embed
                    embed_results = await late_chunk_embed(
                        chunks=texts_to_embed,
                        document_summary=content,
                        embedder=self._embedder,
                        context_prefix_chars=late_ctx_chars,
                        embed_kwargs={"spec": spec, "record_tenant_id": None},
                    )
                    logger.info(
                        "late_chunking_applied",
                        title=title,
                        chunks=len(texts_to_embed),
                        context_chars=late_ctx_chars,
                    )
                except (ValueError, TypeError, RuntimeError, ExternalServiceError) as exc:
                    logger.warning(
                        "late_chunking_failed_fallback",
                        doc=title,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    embed_results = None

            # Fallback: standard per-chunk embedding. The embedder retries
            # internally and raises ExternalServiceError after exhausting
            # attempts; we mark the doc failed and propagate rather than
            # silently storing NULL embeddings (= invisible retrieval hole).
            #
            # Doc-level batching: large documents (thousands of chunks)
            # would otherwise execute one giant await with zero visibility
            # while the embedder-internal HTTP loop iterates dozens of
            # provider calls. Splitting the orchestrator-side loop into
            # ``doc_batch_size`` slices lets us emit progress structlog
            # events + yield to the event loop between provider rounds.
            # Note: the embedder's own retry-with-backoff + circuit breaker
            # still runs inside each ``embed_batch`` call; we do NOT add
            # a second retry layer here.
            # TODO(admin): ``documents`` has no ``chunks_embedded`` /
            # ``embedded_at`` column yet, so per-batch progress is emitted
            # via structlog only. When the column lands in alembic, add an
            # UPDATE here keyed on ``doc_id`` so external observers can
            # poll the DB instead of grepping logs.
            if embed_results is None:
                try:
                    embed_results = await self._embed_in_doc_batches(
                        texts_to_embed,
                        spec=spec,
                        document_id=doc_id,
                        record_bot_id=record_bot_id,
                    )
                except (ExternalServiceError, ValueError, TypeError, RuntimeError) as exc:
                    chunk_indices_failed = [c_idx for c_idx, _, _ in _chunks_needing_embed]
                    logger.error(
                        "embedding_failed_aborting_ingest",
                        doc=title,
                        document_id=str(doc_id),
                        record_bot_id=str(record_bot_id),
                        chunk_index_min=min(chunk_indices_failed) if chunk_indices_failed else None,
                        chunk_index_max=max(chunk_indices_failed) if chunk_indices_failed else None,
                        chunk_count=len(chunk_indices_failed),
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    # Mark document as failed so re-ingest / admin tooling can find it.
                    async with session_with_tenant(
                        self._sf, record_tenant_id=record_tenant_id,
                    ) as session:
                        await session.execute(
                            text("UPDATE documents SET state = 'failed' WHERE id = :id"),
                            {"id": doc_id},
                        )
                        await session.commit()
                    if isinstance(exc, ExternalServiceError):
                        raise
                    raise ExternalServiceError(
                        f"embedding failed for document '{title}': {exc}",
                    ) from exc

            # B-Z5-U7-1 fix: align with the documented contract above
            # ("NEVER silently store NULL embeddings"). If the embedder
            # returned fewer vectors than texts (provider truncation,
            # late_chunking length mismatch, partial-success), raise so
            # the document is marked failed instead of silently inserting
            # rows with NULL embedding that vanish from hybrid search.
            if len(embed_results) != len(_chunks_needing_embed):
                logger.error(
                    "embedding_length_mismatch_aborting_ingest",
                    doc=title,
                    document_id=str(doc_id),
                    record_bot_id=str(record_bot_id),
                    expected=len(_chunks_needing_embed),
                    got=len(embed_results),
                )
                async with session_with_tenant(
                    self._sf, record_tenant_id=record_tenant_id,
                ) as session:
                    await session.execute(
                        text("UPDATE documents SET state = 'failed' WHERE id = :id"),
                        {"id": doc_id},
                    )
                    await session.commit()
                raise ExternalServiceError(
                    f"embedding length mismatch for document '{title}': "
                    f"expected {len(_chunks_needing_embed)}, got {len(embed_results)}",
                )
            for idx_in_batch, (chunk_idx, _, _) in enumerate(_chunks_needing_embed):
                new_embeddings[chunk_idx] = embed_results[idx_in_batch]

            if _audit is not None:
                # One summary event per ingest — per-chunk embed events
                # would explode the file with little extra value.
                _dim = 0
                if embed_results and embed_results[0] is not None:
                    try:
                        _dim = len(embed_results[0])
                    except TypeError:
                        _dim = 0
                await _audit.log(
                    _audit_bot,
                    "ingest",
                    "embedding_generated",
                    {
                        "model": spec.model_name,
                        "provider": spec.provider,
                        "dim": _dim,
                        "n_embedded": len(_chunks_needing_embed),
                        "late_chunking": late_chunking_enabled,
                        "late_chunking_sliding": use_sliding,
                    },
                )

        # ── Ingestion quality validation (advisory — logs only) ──
        validation_enabled = True
        validation_min_chars = 20
        if self._cfg is not None:
            validation_enabled = bool(
                await self._cfg.get("ingestion_validation_enabled", True),
            )
            validation_min_chars = await self._cfg.get_int(
                "ingestion_min_chunk_chars", 20,
            )

        if validation_enabled:
            validation_chunks = [
                {
                    "content": chunk_text,
                    "embedding": new_embeddings.get(i),
                }
                for i, chunk_text in enumerate(chunks)
            ]
            validation_result = await validate_ingestion(
                validation_chunks,
                title,
                original_content_length=len(content),
                min_chunk_chars=validation_min_chars,
            )
            if not validation_result["ok"]:
                logger.warning(
                    "ingestion_validation_issues",
                    document=title,
                    score=validation_result["score"],
                    issues=validation_result["issues"],
                )
            else:
                logger.info(
                    "ingestion_validation_passed",
                    document=title,
                    score=validation_result["score"],
                )

        # M21 — per-bot opt-in for deterministic chunk UUID5. Resolved
        # once before the DB write loop so the three insert paths
        # (parent_rows / child_rows / flat rows) all share one factory
        # closure. Default OFF preserves legacy ``uuid.uuid4()`` semantics.
        _chunk_hash_id_enabled = await self._resolve_chunk_hash_id_enabled(
            record_bot_id=record_bot_id,
            record_tenant_id=record_tenant_id,
        )
        if _chunk_hash_id_enabled:
            from ragbot.shared.chunk_identity import deterministic_chunk_id

            def _make_chunk_id(content: str) -> uuid.UUID:
                """Deterministic UUID5 — same content → same UUID → idempotent UPSERT."""
                return deterministic_chunk_id(
                    record_bot_id=record_bot_id,
                    document_id=doc_id,
                    content=content,
                )
        else:
            from ragbot.shared.chunk_identity import time_ordered_chunk_id  # noqa: PLC0415

            def _make_chunk_id(_content: str) -> uuid.UUID:
                """Time-ordered UUIDv7 — sequential PK insert locality vs v4 scatter."""
                return time_ordered_chunk_id()

        # M23 — content-type dispatch histogram. Default ON because the
        # cost is a single pass over already-computed chunks (no LLM,
        # no DB). Pure observability — no chunking behaviour changes
        # here. Wave 4 will add per-type strategy routing.
        #
        # Delegates to ``content_type_router`` so the {group_by_type +
        # emit_histogram} pair stays a single source of truth across
        # ingest / future per-type chunkers. The splitter returns
        # ``(block_type, content)`` tuples — we wrap each as a
        # ``SimpleNamespace`` to satisfy the helper's getattr contract
        # without inventing a heavier DTO for an observability hop.
        if DEFAULT_CONTENT_TYPE_DISPATCH_ENABLED and chunks:
            try:
                from types import SimpleNamespace

                # ``_split_into_blocks_with_atomic`` is already imported at
                # module scope (top of file). A redundant function-local
                # ``from ... import`` here would rebind the name to a local
                # slot, causing UnboundLocalError at the earlier M25 use site
                # because Python detects the local binding at compile time.
                _blocks: list[SimpleNamespace] = []
                for _c in chunks:
                    for _btype, _ in _split_into_blocks_with_atomic(_c):
                        _blocks.append(SimpleNamespace(block_type=_btype))
                _groups = group_by_block_type(_blocks)
                emit_type_histogram(_groups, document_id=str(doc_id))
            except (ValueError, TypeError, RuntimeError) as exc:
                # Observability-only — must not break ingest. Narrow
                # exceptions per CLAUDE.md broad-except policy.
                logger.warning(
                    "ingest_blocks_by_type_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # Database operations: delete stale + delete changed + insert new/changed
        # LEGAL-RETRIEVAL-FIX Phase 1 2026-05-20: resolve structured-ref
        # extraction flag once before the chunk-write loop. Gate the 3 wire
        # sites below so an operator that opts out (e.g. non-VN corpora
        # where the regex would only burn CPU) can flip the flag without
        # touching code. Default ON via shared/constants SSoT.
        # ┌─ NANO-IN-INGEST PATH #5 of N — DEFAULT OFF (system_config
        # │  structured_ref_extraction_enabled=false, alembic 0231) ─────────────
        # │  WHY OFF: legal Điều/Chương reference extraction sends the FULL legal
        # │  doc to the LLM — heavy on banking/legal corpora, contributed to the
        # │  legal-doc ingest stall. Redundant with Jina late_chunking for
        # │  retrieval context. NOTE: the in-constant DEFAULT stays True (the
        # │  fallback when no config row exists); the seeded row (0231) is the
        # │  source of truth = OFF. If legal-article recall drops at query time,
        # │  re-enable JUST this (it is cheaper than CR-enhanced #4).
        # └──────────────────────────────────────────────────────────────────────
        _struct_ref_extract_on: bool = DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED
        if self._cfg is not None:
            try:
                _struct_ref_extract_on = bool(
                    await self._cfg.get(
                        "structured_ref_extraction_enabled",
                        DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED,
                    ),
                )
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning(
                    "structured_ref_extraction_flag_lookup_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    fallback=DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED,
                )

        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            # Delete stale chunks (indices beyond new chunk count)
            if stale_indices:
                await session.execute(
                    text("""DELETE FROM document_chunks
                            WHERE record_document_id = :doc_id
                            AND chunk_index >= :min_stale"""),
                    {"doc_id": doc_id, "min_stale": len(chunks)},
                )

            # Delete changed chunks (will be re-inserted with new content)
            changed_indices = [i for i, _, _ in chunks_to_embed if i in existing_hashes]
            if changed_indices:
                await session.execute(
                    text("""DELETE FROM document_chunks
                            WHERE record_document_id = :doc_id
                            AND chunk_index = ANY(:indices)"""),
                    {"doc_id": doc_id, "indices": changed_indices},
                )

            # ── Parent-child insertion: parents first, then children with FK ──
            if parent_child_enabled and pc_hierarchy:
                # Build lookup: chunk_index -> hierarchy item
                _pc_lookup = {item["chunk_index"]: item for item in pc_hierarchy}
                # Map parent_global_index -> inserted UUID
                _parent_id_map: dict[int, uuid.UUID] = {}

                # Phase 1: Insert parent chunks (no embedding, no parent_chunk_id)
                parent_rows = []
                for chunk_idx, chunk_text, chunk_hash in chunks_to_embed:
                    if chunk_idx not in _pc_parent_indices:
                        continue
                    enriched_prefix = ""
                    if chunk_idx < len(enriched_chunks):
                        pos = enriched_chunks[chunk_idx].find(chunk_text)
                        if pos > 0:
                            enriched_prefix = enriched_chunks[chunk_idx][:pos][
                                :DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS
                            ]
                    chunk_meta: dict[str, Any] = {
                        "chunk_index": chunk_idx,
                        "total_chunks": len(chunks),
                        "document_title": title,
                        "enriched_prefix": enriched_prefix,
                        "is_parent_chunk": True,
                        "chunk_strategy": "parent_child",
                    }
                    if chunk_idx < len(_quality_scores):
                        chunk_meta["quality_score"] = _quality_scores[chunk_idx]
                    if _chunking_strategy == "hdt":
                        sp = extract_structural_path(chunk_text)
                        if sp["structural_path"] is not None:
                            chunk_meta["structural_path"] = sp["structural_path"]
                    if extracted_metadata:
                        chunk_meta["extracted_metadata"] = extracted_metadata
                    # T1: persist enriched text into ``content`` so BM25 +
                    # rerank both see the prefix. Pre-enrichment text is kept
                    # in metadata.raw_chunk for citation reconstruction.
                    persisted_text = (
                        persist_chunks[chunk_idx]
                        if chunk_idx < len(persist_chunks)
                        else chunk_text
                    )
                    if enriched_persist_enabled and persisted_text != chunk_text:
                        chunk_meta["raw_chunk"] = chunk_text
                        chunk_meta["enriched_prefix_persisted"] = True
                    # T1-Smartness 2026-05-21 (LEGAL-RETRIEVAL-FIX Phase 1 fix):
                    # populate structural-anchor keys (article_no, chapter_no,
                    # section_no, clause_no, appendix_no) for ArticleAwareFilter
                    # JSONB-containment pre-filter. Extract from ``persisted_text``
                    # (post-CR enriched) — its leading enrichment prefix names the
                    # canonical Article of the chunk, while raw ``chunk_text``
                    # often inlines the HDT structural-path crumb "Điều X > Điều Y"
                    # whose first-match yields the parent path, not the leaf.
                    # First-match-wins. Gated by ``_struct_ref_extract_on``.
                    if _struct_ref_extract_on:
                        _struct_refs = extract_structured_refs(persisted_text)
                        if _struct_refs:
                            chunk_meta.update(_struct_refs)
                    # M21: deterministic UUID5 when per-bot flag is set,
                    # else legacy uuid.uuid4() — factory closure resolved
                    # once before the DB write loop above.
                    row_id = _make_chunk_id(persisted_text)
                    _parent_id_map[chunk_idx] = row_id
                    parent_rows.append({
                        "id": row_id,
                        "doc_id": doc_id,
                        "idx": chunk_idx,
                        "content": persisted_text,
                        "content_segmented": (
                            _segmented_chunks[chunk_idx]
                            if chunk_idx < len(_segmented_chunks) else None
                        ),
                        "hash": chunk_hash,
                        "emb": None,  # parents have no embedding
                        "meta": json.dumps(chunk_meta),
                        "chunk_chars": len(persisted_text),
                        # M10 — pre-classify modality so the row inserts
                        # the correct first-class ``chunk_type`` value.
                        "chunk_type": chunk_type_for(
                            persisted_text,
                            is_table_row=(_chunking_strategy == "table_csv"),
                        ),
                    })
                if parent_rows:
                    # Single embedding column — provider binding carries
                    # dim metadata, no per-write column routing.
                    await _bulk_insert_chunks(
                        session, parent_rows,
                        record_bot_id=record_bot_id,
                        embedding_column=DEFAULT_EMBEDDING_COLUMN,
                    )

                # Phase 2: Insert child chunks (with embedding + parent_chunk_id FK)
                child_rows = []
                for chunk_idx, chunk_text, chunk_hash in chunks_to_embed:
                    if chunk_idx in _pc_parent_indices:
                        continue
                    pc_item = _pc_lookup.get(chunk_idx, {})
                    parent_global_idx = pc_item.get("parent_global_index")
                    parent_chunk_id = _parent_id_map.get(parent_global_idx) if parent_global_idx is not None else None
                    emb = new_embeddings.get(chunk_idx)
                    enriched_prefix = ""
                    if chunk_idx < len(enriched_chunks):
                        pos = enriched_chunks[chunk_idx].find(chunk_text)
                        if pos > 0:
                            enriched_prefix = enriched_chunks[chunk_idx][:pos][
                                :DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS
                            ]
                    chunk_meta: dict[str, Any] = {
                        "chunk_index": chunk_idx,
                        "total_chunks": len(chunks),
                        "document_title": title,
                        "enriched_prefix": enriched_prefix,
                        "is_child_chunk": True,
                        "parent_chunk_index": parent_global_idx,
                        "chunk_strategy": "parent_child",
                    }
                    if chunk_idx < len(_quality_scores):
                        chunk_meta["quality_score"] = _quality_scores[chunk_idx]
                    if cr_active and chunk_idx < len(cr_raw_chunks):
                        chunk_meta["contextual_retrieval"] = True
                    if _chunking_strategy == "hdt":
                        sp = extract_structural_path(chunk_text)
                        if sp["structural_path"] is not None:
                            chunk_meta["structural_path"] = sp["structural_path"]
                    if extracted_metadata:
                        chunk_meta["extracted_metadata"] = extracted_metadata
                    # T1: persist enriched text into ``content`` so BM25 +
                    # rerank both see the prefix.
                    persisted_text = (
                        persist_chunks[chunk_idx]
                        if chunk_idx < len(persist_chunks)
                        else chunk_text
                    )
                    if enriched_persist_enabled and persisted_text != chunk_text:
                        chunk_meta.setdefault("raw_chunk", chunk_text)
                        chunk_meta["enriched_prefix_persisted"] = True
                    # T1-Smartness 2026-05-21 (LEGAL-RETRIEVAL-FIX Phase 1 fix):
                    # populate structural-anchor keys for ArticleAwareFilter
                    # (child-chunk loop — parent_child mode). Extract from
                    # ``persisted_text`` (post-CR) — see parent-loop comment.
                    if _struct_ref_extract_on:
                        _struct_refs = extract_structured_refs(persisted_text)
                        if _struct_refs:
                            chunk_meta.update(_struct_refs)
                    # Wave E3 — attach narrate-then-embed metadata when the
                    # service was wired. ``raw_chunk`` is set with setdefault
                    # so CR's pre-enrichment text (a stronger citation source)
                    # wins; ``narrated_text`` + ``block_type`` are always set
                    # so retrieval / eval can introspect the embed-target.
                    _narrate_meta = _narrate_meta_by_idx.get(chunk_idx)
                    if _narrate_meta is not None:
                        chunk_meta.setdefault(
                            NARRATE_METADATA_KEY_RAW_CHUNK,
                            _narrate_meta[NARRATE_METADATA_KEY_RAW_CHUNK],
                        )
                        chunk_meta[NARRATE_METADATA_KEY_NARRATED_TEXT] = (
                            _narrate_meta[NARRATE_METADATA_KEY_NARRATED_TEXT]
                        )
                        chunk_meta[NARRATE_METADATA_KEY_BLOCK_TYPE] = (
                            _narrate_meta[NARRATE_METADATA_KEY_BLOCK_TYPE]
                        )
                    child_rows.append({
                        "id": _make_chunk_id(persisted_text),
                        "doc_id": doc_id,
                        "idx": chunk_idx,
                        "content": persisted_text,
                        "content_segmented": (
                            _segmented_chunks[chunk_idx]
                            if chunk_idx < len(_segmented_chunks) else None
                        ),
                        "hash": chunk_hash,
                        "emb": str(emb) if emb else None,
                        "meta": json.dumps(chunk_meta),
                        "parent_chunk_id": parent_chunk_id,
                        "chunk_chars": len(persisted_text),
                        "chunk_type": chunk_type_for(
                            persisted_text,
                            is_table_row=(_chunking_strategy == "table_csv"),
                        ),
                        # WA-3 — Enhanced CR storage column. Populated only
                        # when the bot opted in via plan_limits; otherwise
                        # NULL (column nullable by design in alembic 010l).
                        "chunk_context": (
                            chunk_contexts[chunk_idx]
                            if chunk_idx < len(chunk_contexts) and chunk_contexts[chunk_idx]
                            else None
                        ),
                    })
                if child_rows:
                    await _bulk_insert_chunks(
                        session, child_rows,
                        record_bot_id=record_bot_id,
                        has_parent_chunk_id=True,
                        embedding_column=DEFAULT_EMBEDDING_COLUMN,
                    )

                rows = parent_rows + child_rows
            else:
                # Standard flat insertion (no parent-child)
                rows = []
                for chunk_idx, chunk_text, chunk_hash in chunks_to_embed:
                    emb = new_embeddings.get(chunk_idx)
                    enriched_prefix = ""
                    if chunk_idx < len(enriched_chunks):
                        pos = enriched_chunks[chunk_idx].find(chunk_text)
                        if pos > 0:
                            enriched_prefix = enriched_chunks[chunk_idx][:pos][
                                :DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS
                            ]
                    chunk_meta: dict[str, Any] = {
                        "chunk_index": chunk_idx,
                        "total_chunks": len(chunks),
                        "document_title": title,
                        "enriched_prefix": enriched_prefix,
                        "chunking_strategy": _chunking_strategy,
                        "chunking_confidence": _chunking_confidence,
                    }
                    if chunk_idx < len(_quality_scores):
                        chunk_meta["quality_score"] = _quality_scores[chunk_idx]
                    if cr_active and chunk_idx < len(cr_raw_chunks):
                        chunk_meta["contextual_retrieval"] = True
                    # Extract structural_path from HDT chunks
                    if _chunking_strategy == "hdt":
                        sp = extract_structural_path(chunk_text)
                        if sp["structural_path"] is not None:
                            chunk_meta["structural_path"] = sp["structural_path"]
                    if extracted_metadata:
                        chunk_meta["extracted_metadata"] = extracted_metadata
                    if is_whole_document:
                        chunk_meta["is_full_document"] = True
                        chunk_meta["original_char_count"] = len(content)
                        chunk_meta["chunk_strategy"] = "whole_document"
                    # T1: persist enriched text into ``content`` so BM25 +
                    # rerank both see the prefix.
                    persisted_text = (
                        persist_chunks[chunk_idx]
                        if chunk_idx < len(persist_chunks)
                        else chunk_text
                    )
                    if enriched_persist_enabled and persisted_text != chunk_text:
                        chunk_meta.setdefault("raw_chunk", chunk_text)
                        chunk_meta["enriched_prefix_persisted"] = True
                    # T1-Smartness 2026-05-21 (LEGAL-RETRIEVAL-FIX Phase 1 fix):
                    # populate structural-anchor keys for ArticleAwareFilter
                    # (single-chunk loop — non-parent-child default mode).
                    # Extract from ``persisted_text`` — see parent-loop comment.
                    if _struct_ref_extract_on:
                        _struct_refs = extract_structured_refs(persisted_text)
                        if _struct_refs:
                            chunk_meta.update(_struct_refs)
                    # Wave E3 — attach narrate-then-embed metadata when the
                    # service was wired. ``raw_chunk`` is set with setdefault
                    # so CR's pre-enrichment text (a stronger citation source)
                    # wins; ``narrated_text`` + ``block_type`` are always set
                    # so retrieval / eval can introspect the embed-target.
                    _narrate_meta = _narrate_meta_by_idx.get(chunk_idx)
                    if _narrate_meta is not None:
                        chunk_meta.setdefault(
                            NARRATE_METADATA_KEY_RAW_CHUNK,
                            _narrate_meta[NARRATE_METADATA_KEY_RAW_CHUNK],
                        )
                        chunk_meta[NARRATE_METADATA_KEY_NARRATED_TEXT] = (
                            _narrate_meta[NARRATE_METADATA_KEY_NARRATED_TEXT]
                        )
                        chunk_meta[NARRATE_METADATA_KEY_BLOCK_TYPE] = (
                            _narrate_meta[NARRATE_METADATA_KEY_BLOCK_TYPE]
                        )
                    rows.append({
                        "id": _make_chunk_id(persisted_text),
                        "doc_id": doc_id,
                        "idx": chunk_idx,
                        "content": persisted_text,
                        "content_segmented": (
                            _segmented_chunks[chunk_idx]
                            if chunk_idx < len(_segmented_chunks) else None
                        ),
                        "hash": chunk_hash,
                        "emb": str(emb) if emb else None,
                        "meta": json.dumps(chunk_meta),
                        "chunk_chars": len(persisted_text),
                        "chunk_type": chunk_type_for(
                            persisted_text,
                            is_table_row=(_chunking_strategy == "table_csv"),
                        ),
                        # WA-3 — Enhanced CR storage column. See parent-child
                        # path above for the contract.
                        "chunk_context": (
                            chunk_contexts[chunk_idx]
                            if chunk_idx < len(chunk_contexts) and chunk_contexts[chunk_idx]
                            else None
                        ),
                    })
                if rows:
                    await _bulk_insert_chunks(
                        session, rows,
                        record_bot_id=record_bot_id,
                        embedding_column=DEFAULT_EMBEDDING_COLUMN,
                    )
            await session.commit()

        # P24-L1: invalidate semantic_cache when the corpus actually mutates.
        # `bot_version`/`corpus_version` default 'latest' forever after migration 0011
        # — there is no version counter, so the only safe invalidation signal is a
        # DELETE here whenever chunks were inserted/changed/removed.
        _mutated = bool(chunks_to_embed) or bool(stale_indices) or bool(changed_indices)
        if _mutated:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                rc = await session.execute(
                    text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
                    {"bid": record_bot_id},
                )
                await session.commit()
                logger.info(
                    "semantic_cache_invalidated",
                    reason="ingest_mutation",
                    record_bot_id=str(record_bot_id),
                    document_id=str(doc_id),
                    is_reindex=is_reindex,
                    rows_deleted=rc.rowcount or 0,
                )

        any_embedded = any(v is not None for v in new_embeddings.values()) if new_embeddings else False

        # ── U7 ingest_embed_store — record Phase D row at embed+store boundary ──
        # Trailing-record pattern: embedding + bulk insertion above (parent-
        # child split or flat) bleed into many variables; metadata reports
        # the captured ``duration_ms_actual`` covering both external embed
        # API time and DB INSERT throughput.
        _u7_dur_ms = int((time.perf_counter() - _u7_t0) * 1000)
        async with _phase_d_step(step_tracker, "ingest_embed_store") as _u7_ctx:
            _u7_n_null_embed = sum(
                1 for v in new_embeddings.values() if v is None
            )
            _u7_ctx.set_metadata(
                n_chunks_embedded=len(new_embeddings),
                n_chunks_stored=len(chunks),
                n_null_embedding=_u7_n_null_embed,
                embedding_model=spec.model_name if spec else "",
                embedding_dim=spec.dimension if spec else 0,
                is_reindex=is_reindex,
                duration_ms_actual=_u7_dur_ms,
            )
        ctx.new_embeddings = new_embeddings
        ctx.narrate_meta_by_idx = _narrate_meta_by_idx
        ctx.spec = spec
        ctx.rows = rows
        ctx.any_embedded = any_embedded
