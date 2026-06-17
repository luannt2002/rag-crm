"""Ingest U5 enrich stage — split from ingest_stages to keep files navigable."""
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


class _StageEnrichMixin:
    async def _stage_u5_enrich(self, ctx: _IngestCtx) -> None:
        content = ctx.content
        title = ctx.title
        language = ctx.language
        step_tracker = ctx.step_tracker
        chunks = ctx.chunks
        parent_child_enabled = ctx.parent_child_enabled
        pc_hierarchy = ctx.pc_hierarchy
        _chunking_strategy = ctx.chunking_strategy
        record_bot_id = ctx.record_bot_id
        record_tenant_id = ctx.record_tenant_id
        # ── U5 + U6 config reads — parallelised (no data dependency between
        # CR config keys and VN-segment config keys; gather halves round trips
        # to Redis / DB config layer).
        _u5_t0 = time.perf_counter()

        # Contextual Retrieval (Anthropic 2024-09).
        # Per-chunk LLM rewrite: each chunk gets a 50-100 token context
        # prefix derived from the parent doc, then embedded. -49% retrieval
        # failure (Anthropic study). Opt-in via system_config; defaults ON
        # but the per-chunk LLM call only runs when both flag and model are
        # set, and the doc is below the cost-guard char cap.
        cr_enabled = DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED
        cr_model: str = ""
        cr_max_tokens = DEFAULT_CR_CONTEXT_MAX_TOKENS
        cr_cache = DEFAULT_CR_PROMPT_CACHE_ENABLED
        cr_max_doc_chars = DEFAULT_CR_MAX_DOC_CHARS
        vi_seg_enabled = DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED
        vi_seg_timeout = DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S
        enrich_row_gate = DEFAULT_ENRICH_ROW_GATE_ENABLED
        if self._cfg is not None:
            # Gather CR + VN-segment config reads in parallel — independent keys.
            (
                _cr_enabled_raw,
                _cr_model_raw,
                cr_max_tokens,
                _cr_cache_raw,
                cr_max_doc_chars,
                _vi_seg_enabled_raw,
                vi_seg_timeout,
                _enrich_row_gate_raw,
            ) = await asyncio.gather(
                self._cfg.get("contextual_retrieval_enabled", DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED),
                self._cfg.get("contextual_retrieval_model", _DEFAULT_CR_FALLBACK_MODEL),
                self._cfg.get_int("contextual_retrieval_context_max_tokens", DEFAULT_CR_CONTEXT_MAX_TOKENS),
                self._cfg.get("contextual_retrieval_prompt_cache_enabled", DEFAULT_CR_PROMPT_CACHE_ENABLED),
                self._cfg.get_int("contextual_retrieval_max_doc_chars", DEFAULT_CR_MAX_DOC_CHARS),
                self._cfg.get("vi_compound_segmentation_ingest_enabled", DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED),
                self._cfg.get_int("vi_compound_segmentation_timeout_s", DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S),
                self._cfg.get("enrich_row_gate_enabled", DEFAULT_ENRICH_ROW_GATE_ENABLED),
            )
            cr_enabled = bool(_cr_enabled_raw)
            cr_model = str(_cr_model_raw)
            cr_max_tokens = int(cr_max_tokens)
            cr_cache = bool(_cr_cache_raw)
            cr_max_doc_chars = int(cr_max_doc_chars)
            vi_seg_enabled = bool(_vi_seg_enabled_raw)
            vi_seg_timeout = int(vi_seg_timeout)
            enrich_row_gate = bool(_enrich_row_gate_raw)

        # ── CR / per-chunk-enrich ROW GATE (2026-06-13) ─────────────────
        # Tabular strategies (table_csv / table_dual_index) emit one chunk
        # per data row; each row already carries its header + key:value
        # structure, so the per-chunk LLM enrichment lift is ~0 while the
        # call count dominates ingest latency/cost (a 225K-char sheet =
        # hundreds of rows × 1 LLM call). When the gate is on and the
        # resolved strategy is tabular, ALL three per-chunk enrichment paths
        # below (inline CR, legacy enrich_chunks, chunk_context storage) are
        # skipped. Rows stay fully searchable via header + BM25. Config-flip
        # ``enrich_row_gate_enabled=false`` rolls back with no redeploy.
        skip_row_enrich = should_skip_row_enrich(
            _chunking_strategy, gate_enabled=enrich_row_gate,
        )
        if skip_row_enrich:
            logger.info(
                "enrich_row_gate_skip",
                strategy=_chunking_strategy,
                n_chunks=len(chunks),
                doc_chars=len(content),
            )

        # ┌─ NANO-IN-INGEST PATH #1 of 3 — DEFAULT OFF (system_config
        # │  contextual_retrieval_enabled=false, alembic 0228) ──────────────────
        # │  WHY OFF: per-chunk nano CR sends the FULL doc as context per chunk →
        # │  O(n^2) tokens (measured: 1 doc = 1.54M tok / ~8 min / saturates the
        # │  200k OpenAI TPM). Jina embedder's late_chunking now supplies the same
        # │  cross-chunk context INSIDE the embed pass with ZERO LLM calls, so
        # │  this path is redundant. Kept (not deleted) for config-reversible
        # │  rollback if late_chunking is ever switched off. The other two nano
        # │  paths are ``enrich_enabled`` (below) and narrate (document_worker).
        # └──────────────────────────────────────────────────────────────────────
        # Cost guard: doc longer than threshold → skip CR for the whole doc
        # (returning original chunks) so ingest cost stays bounded. Row gate
        # additionally short-circuits CR for self-describing tabular rows.
        cr_active = (
            cr_enabled
            and bool(cr_model)
            and len(content) <= cr_max_doc_chars
            and not skip_row_enrich
        )
        cr_raw_chunks: list[str] = list(chunks)  # snapshot pre-enrichment

        # ── WA-3 Enhanced CR storage path — per-bot opt-in ──────────────
        # Resolve per-bot ``plan_limits.cr_enhanced_enabled`` (default
        # OFF). When ON, run ``ChunkContextEnricher`` against the raw
        # chunk snapshot to obtain a SEPARATE situated-context string
        # per chunk (storage column ``chunk_context`` — alembic 010l).
        # Result is held in ``chunk_contexts`` aligned to ``cr_raw_chunks``;
        # ``_bulk_insert_chunks`` reads from this list when populating
        # the row. STORAGE-ONLY — the application never prepends these
        # strings to the LLM answer prompt (Quality Gate #10).
        chunk_contexts: list[str] = []
        # ┌─ NANO-IN-INGEST PATH #4 of N — DEFAULT OFF (system_config
        # │  cr_enhanced_enabled=false, alembic 0231) ───────────────────────────
        # │  WHY OFF: this is a SECOND contextual-retrieval implementation (WA-3
        # │  "Enhanced CR") that runs INDEPENDENTLY of contextual_retrieval_enabled
        # │  (#1). It was the real legal-doc blocker after #1–#3 were off: per-chunk
        # │  nano with full-doc context = 19k tokens/call = O(n^2) storm, chunks=0
        # │  until it finished. Jina late_chunking supplies structural context now,
        # │  so this is redundant. Two CR impls existed — disabling #1 alone left
        # │  this one firing (the "whack-a-mole" root cause). Re-enable ONLY without
        # │  Jina late_chunking.
        # └──────────────────────────────────────────────────────────────────────
        _cr_system_default: bool = DEFAULT_CR_ENHANCED_ENABLED
        if self._cfg is not None:
            try:
                _cr_system_default = bool(
                    await self._cfg.get(
                        "cr_enhanced_enabled",
                        DEFAULT_CR_ENHANCED_ENABLED,
                    ),
                )
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning(
                    "cr_enhanced_system_default_lookup_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    fallback=DEFAULT_CR_ENHANCED_ENABLED,
                )
        cr_enhanced_on = _cr_system_default
        if self._bot_repo is not None and record_tenant_id is not None:
            try:
                _wa3_bot_cfg = await self._bot_repo.get_by_id(
                    record_bot_id,
                    record_tenant_id=record_tenant_id,
                )
            except (AttributeError, TypeError, ValueError) as exc:
                _wa3_bot_cfg = None
                logger.warning(
                    "chunk_context_bot_cfg_lookup_failed",
                    record_bot_id=str(record_bot_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            if _wa3_bot_cfg is not None:
                cr_enhanced_on = bool(
                    resolve_bot_limit(
                        _wa3_bot_cfg,
                        "cr_enhanced_enabled",
                        system_default=_cr_system_default,
                    ),
                )

        if cr_enhanced_on and cr_raw_chunks and not skip_row_enrich:
            chunk_contexts = (
                await self._chunk_context_enricher.generate_contexts(
                    content,
                    cr_raw_chunks,
                )
            )
            logger.info(
                "chunk_context_enrichment_applied",
                title=title,
                n_chunks=len(cr_raw_chunks),
                n_contexts_non_empty=sum(
                    1 for c in chunk_contexts if c
                ),
                provider=self._chunk_context_enricher.provider_name,
            )

        # Gate VN compound segmentation on language; underthesea is VN-only,
        # running it on other languages burns CPU without changing tokens.
        _effective_language = language if language != "auto" else DEFAULT_LANGUAGE
        _vi_seg_lang_eligible = _effective_language in VI_DOMAIN_LANGUAGES

        # Sentinel: U6 segmentation results pre-computed in CR path (avoids
        # second pass over the same chunks); None means U6 must run its own
        # pass after enrichment (legacy enrich + non-CR paths).
        _u6_precomputed: list[str | None] | None = None

        if cr_active:
            # Skip parents in parent-child mode — only rewrite leaf/child
            # chunks (parents are big and not embedded).
            _cr_skip_indices: set[int] = set()
            if parent_child_enabled and pc_hierarchy:
                _cr_skip_indices = {
                    item["chunk_index"]
                    for item in pc_hierarchy
                    if item.get("is_parent")
                }

            # U5 ∥ U6 per-chunk gather: CR enrich (async LLM network call)
            # and VN segment (sync CPU via asyncio.to_thread) are independent
            # for the same original chunk text. Both are dispatched in a
            # single asyncio.gather per chunk so wall-time = max(CR, seg)
            # instead of CR + seg for each chunk. Semaphore caps LLM
            # concurrency; thread pool caps VN-segment concurrency naturally.
            #
            # Trade-off: VN segment runs on the ORIGINAL chunk text, not the
            # enriched text. Enrichment adds a context PREFIX; the Vietnamese
            # compound words being tokenised live in the original body, so
            # BM25 quality is preserved. content_segmented will carry the
            # segmented original; content carries the enriched form.
            _cr_sem = asyncio.Semaphore(DEFAULT_ENRICHMENT_MAX_CONCURRENCY)

            async def _enrich_one(idx: int, original: str) -> str:
                if idx in _cr_skip_indices:
                    return original
                async with _cr_sem:
                    return await enrich_chunk_with_context(
                        original,
                        content,
                        model_id=cr_model,
                        max_context_tokens=cr_max_tokens,
                        prompt_cache_enabled=cr_cache,
                        max_doc_chars=cr_max_doc_chars,
                    )

            async def _vn_seg_one(original: str) -> str:
                """Wrap sync segment_vi_compounds for asyncio.to_thread."""
                if not (vi_seg_enabled and _vi_seg_lang_eligible):
                    return original  # gate closed — caller keeps original
                return await asyncio.to_thread(
                    segment_vi_compounds, original, timeout_s=vi_seg_timeout,
                )

            async def _enrich_and_segment(
                idx: int, original: str,
            ) -> tuple[str, str]:
                """Run CR enrich + VN segment concurrently on the same chunk.

                Returns (enriched_text, segmented_text). Both ops receive the
                original chunk so they are independent (no data dependency).
                """
                enr, seg = await asyncio.gather(
                    _enrich_one(idx, original),
                    _vn_seg_one(original),
                    return_exceptions=True,
                )
                # Partial-failure: if CR fails → fall back to original; if
                # VN-seg fails → fall back to original (no compound split).
                enr_out: str = original if isinstance(enr, BaseException) else enr  # type: ignore[assignment]
                seg_out: str = original if isinstance(seg, BaseException) else seg  # type: ignore[assignment]
                return enr_out, seg_out

            # Prompt-cache warm-up: the full-doc CR prefix is identical across
            # every chunk and is auto-cached by the provider, but a Semaphore-wide
            # concurrent burst races before the first response seeds that cache —
            # the opening wave then caches only ~26-54% vs ~97% once warm. Seed
            # ONE enrich sequentially (its result is kept, not wasted), then fan
            # out the rest. Skipped for tiny docs where the +1 round-trip isn't
            # worth it, and when CR caching is off.
            _warm_idx = next(
                (i for i in range(len(chunks)) if i not in _cr_skip_indices),
                None,
            )
            if (
                _warm_idx is not None
                and cr_cache
                and len(chunks) > DEFAULT_CR_CACHE_WARM_MIN_CHUNKS
            ):
                _warm_pair = await _enrich_and_segment(_warm_idx, chunks[_warm_idx])
                _rest = await asyncio.gather(
                    *[
                        _enrich_and_segment(idx, orig)
                        for idx, orig in enumerate(chunks)
                        if idx != _warm_idx
                    ],
                )
                _rest_iter = iter(_rest)
                _combined = [
                    _warm_pair if idx == _warm_idx else next(_rest_iter)
                    for idx in range(len(chunks))
                ]
            else:
                _combined = await asyncio.gather(  # type: ignore[assignment]
                    *[_enrich_and_segment(idx, orig) for idx, orig in enumerate(chunks)],
                )

            enriched_results = [pair[0] for pair in _combined]
            _seg_raw = [pair[1] for pair in _combined]

            cr_count = sum(
                1 for idx, (orig, enr) in enumerate(zip(chunks, enriched_results))
                if idx not in _cr_skip_indices and enr != orig
            )
            cr_failed = sum(
                1 for idx, (orig, enr) in enumerate(zip(chunks, enriched_results))
                if idx not in _cr_skip_indices and enr == orig
            )
            chunks = list(enriched_results)

            # Build U6 precomputed list — only store when text actually changed
            # (mirrors the change-detection logic in the standalone U6 pass).
            if vi_seg_enabled and _vi_seg_lang_eligible:
                _u6_precomputed = [
                    seg if seg != orig else None
                    for orig, seg in zip(chunks, _seg_raw)
                ]
            else:
                _u6_precomputed = [None] * len(chunks)

            logger.info(
                "contextual_retrieval_applied",
                title=title,
                chunks_total=len(chunks),
                chunks_enriched=cr_count,
                chunks_unchanged=cr_failed,
                model=cr_model,
                prompt_cache=cr_cache,
            )

        # Anthropic-style prefix injection enrichment (template fallback when
        # LLM fn missing). Disabled when the CR path already rewrote chunks —
        # running both would double-wrap.
        # ┌─ NANO-IN-INGEST PATH #2 of 3 — DEFAULT OFF (system_config
        # │  enrichment_enabled=false, alembic 0230) ────────────────────────────
        # │  WHY OFF: legacy per-chunk nano enrichment — same redundancy as CR
        # │  (#1 above): Jina late_chunking carries the context now, so this just
        # │  burns OpenAI TPM + blocks embed_store. Kept config-reversible. Do NOT
        # │  re-enable expecting "more context" — re-enabling brings back the
        # │  O(n^2) storm. Re-enable ONLY if late_chunking is turned off.
        # └──────────────────────────────────────────────────────────────────────
        # Primary: system_config, fallback: settings.enrichment
        enrich_fallback = self._settings.enrichment
        if self._cfg is not None:
            enrich_enabled = bool(await self._cfg.get("enrichment_enabled", enrich_fallback.enabled))
            enrich_model = await self._cfg.get("enrichment_model", enrich_fallback.model_name)
            enrich_temperature = await self._cfg.get_float("enrichment_temperature", enrich_fallback.temperature)
            enrich_max_tokens = await self._cfg.get_int("enrichment_max_tokens", enrich_fallback.max_tokens)
            enrich_timeout = await self._cfg.get_int("enrichment_timeout_s", enrich_fallback.timeout_s)
            enrich_doc_preview = await self._cfg.get_int("enrichment_doc_preview_chars", enrich_fallback.doc_preview_chars)
            enrich_chunk_preview = await self._cfg.get_int("enrichment_chunk_preview_chars", enrich_fallback.chunk_preview_chars)
            enrich_max_prefix = await self._cfg.get_int("enrichment_max_prefix_chars", enrich_fallback.max_prefix_chars)
            enrich_use_cache = bool(await self._cfg.get("enrichment_use_cache_pattern", True))
        else:
            enrich_enabled = enrich_fallback.enabled
            enrich_model = enrich_fallback.model_name
            enrich_temperature = enrich_fallback.temperature
            enrich_max_tokens = enrich_fallback.max_tokens
            enrich_timeout = enrich_fallback.timeout_s
            enrich_doc_preview = enrich_fallback.doc_preview_chars
            enrich_chunk_preview = enrich_fallback.chunk_preview_chars
            enrich_max_prefix = enrich_fallback.max_prefix_chars
            enrich_use_cache = True

        llm_fn = None
        if enrich_enabled:
            try:
                import litellm as _litellm

                async def _enrich_llm(system: str, user: str) -> str:
                    resp = await _litellm.acompletion(
                        model=enrich_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=enrich_temperature,
                        max_tokens=enrich_max_tokens,
                        timeout=enrich_timeout,
                    )
                    return resp.choices[0].message.content or ""

                llm_fn = _enrich_llm
            except ImportError:
                logger.info("contextual_enrichment_disabled", reason="litellm not available")
            except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "contextual_enrichment_setup_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        if cr_active:
            # CR already rewrote `chunks`. Surface the same list as
            # `enriched_chunks` so the downstream INSERT path keeps the
            # CR prefix in `content` while still computing the
            # `enriched_prefix` metadata field below.
            enriched_chunks = list(chunks)
        else:
            # Fix 4 (2026-05-13): skip Haiku enrich cho doc nhỏ
            # Doc <50K chars chỉ benefit marginal +5pp recall từ
            # Contextual Retrieval, KHÔNG đáng tốn cost + time.
            # Doc lớn (legal, gov >50K) vẫn enrich full.
            from ragbot.shared.constants import DEFAULT_ENRICHMENT_SKIP_BELOW_CHARS  # noqa: PLC0415
            try:
                _enrich_skip_below = await self._cfg.get_int(
                    "enrichment_skip_below_chars",
                    DEFAULT_ENRICHMENT_SKIP_BELOW_CHARS,
                )
            except Exception:  # noqa: BLE001 — config fallback
                _enrich_skip_below = DEFAULT_ENRICHMENT_SKIP_BELOW_CHARS
            if skip_row_enrich or len(content) < _enrich_skip_below:
                logger.info(
                    "enrichment_skip_small_doc",
                    content_chars=len(content),
                    threshold=_enrich_skip_below,
                    chunks=len(chunks),
                    row_gated=skip_row_enrich,
                )
                enriched_chunks = list(chunks)
            else:
                # Fix 1: max_concurrency from DB config (DB knob > constant)
                try:
                    _enrich_concurrency = await self._cfg.get_int(
                        "enrichment_max_concurrency",
                        DEFAULT_ENRICHMENT_MAX_CONCURRENCY,
                    )
                except Exception:  # noqa: BLE001
                    _enrich_concurrency = DEFAULT_ENRICHMENT_MAX_CONCURRENCY
                enriched_chunks = await enrich_chunks(
                    chunks=chunks,
                    document_title=title,
                    full_document=content,
                    llm_fn=llm_fn,
                    doc_preview_chars=enrich_doc_preview,
                    chunk_preview_chars=enrich_chunk_preview,
                    max_prefix_chars=enrich_max_prefix,
                    use_cache_pattern=enrich_use_cache,
                    max_concurrency=_enrich_concurrency,
                )

        # ── U5 ingest_enrich — record Phase D row at enrich boundary ──
        # Trailing-record pattern: enrichment branches above (CR vs legacy)
        # are tightly coupled with downstream variables (cr_active, llm_fn,
        # cr_raw_chunks); a single async-with would require re-indenting
        # ~165 lines. Metadata reports the captured ``duration_ms_actual``.
        _u5_dur_ms = int((time.perf_counter() - _u5_t0) * 1000)
        async with _phase_d_step(step_tracker, "ingest_enrich") as _u5_ctx:
            _u5_parent_count = sum(
                1 for h in pc_hierarchy if h.get("is_parent")
            ) if pc_hierarchy else 0
            _u5_ctx.set_metadata(
                cr_active=cr_active,
                cr_model=cr_model if cr_model else "",
                enrich_prefix_path_enabled=bool(enrich_enabled and not cr_active),
                parent_child_levels=2 if (parent_child_enabled and pc_hierarchy) else 1,
                parent_count=_u5_parent_count,
                n_chunks_in=len(chunks),
                n_chunks_out=len(enriched_chunks),
                duration_ms_actual=_u5_dur_ms,
            )

        # CleanBase quality scoring — observability ONLY, never rejects a
        # chunk. Score persisted to ``metadata_json.quality_score`` so admin
        # dashboards + retrieval tuning have a per-chunk signal. Sub-threshold
        # chunks emit a structured warn event for downstream alerting.
        _quality_scores: list[float] = [
            score_chunk_quality(c) for c in enriched_chunks
        ]
        # CleanBase quality is observability-only: emit a warn event for
        # sub-threshold chunks but never drop them. The skip-indices set is
        # therefore intentionally empty — the loop below keeps the reference
        # so a future "skip-if-below-threshold" feature flag has a single
        # well-named integration point.
        _chunk_quality_skip_indices: set[int] = set()
        for _q_idx, _q_score in enumerate(_quality_scores):
            if _q_score < DEFAULT_CLEANBASE_QUALITY_THRESHOLD:
                emit_chunk_quality_event(
                    score=_q_score,
                    threshold=DEFAULT_CLEANBASE_QUALITY_THRESHOLD,
                    chunk_index=_q_idx,
                    document_title=title,
                )

        # Persist enriched_prefix into ``content`` column so BM25 and the
        # cross-encoder reranker actually see the enrichment (both index
        # off content directly). Original ``raw_chunk`` is preserved in
        # ``metadata_json.raw_chunk`` for citation reconstruction.
        if self._cfg is not None:
            enriched_persist_enabled = bool(
                await self._cfg.get(
                    "enriched_prefix_persist_in_content",
                    DEFAULT_ENRICHED_PREFIX_PERSIST,
                ),
            )
        else:
            enriched_persist_enabled = DEFAULT_ENRICHED_PREFIX_PERSIST

        if enriched_persist_enabled:
            # Both CR and enrich_chunks paths return
            # "{prefix}\n\n{chunk}" (or wrapped tags). Use that text as the
            # canonical persisted content so BM25 + rerank both see it.
            persist_chunks: list[str] = list(enriched_chunks)
        else:
            persist_chunks = list(chunks)
        ctx.chunks = chunks
        ctx.cr_raw_chunks = cr_raw_chunks
        ctx.chunk_contexts = chunk_contexts
        ctx.cr_active = cr_active
        ctx.cr_model = cr_model
        ctx.enrich_enabled = enrich_enabled
        ctx.u6_precomputed = _u6_precomputed
        ctx.quality_scores = _quality_scores
        ctx.chunk_quality_skip_indices = _chunk_quality_skip_indices
        ctx.vi_seg_enabled = vi_seg_enabled
        ctx.vi_seg_timeout = vi_seg_timeout
        ctx.vi_seg_lang_eligible = _vi_seg_lang_eligible
        ctx.effective_language = _effective_language
        ctx.skip_row_enrich = skip_row_enrich
        ctx.enriched_persist_enabled = enriched_persist_enabled
        ctx.persist_chunks = persist_chunks
        ctx.enriched_chunks = enriched_chunks
