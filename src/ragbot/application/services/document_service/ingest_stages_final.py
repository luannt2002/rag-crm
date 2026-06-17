"""Ingest finalize stage — split from ingest_stages to keep files navigable."""
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


class _StageFinalizeMixin:
    async def _stage_finalize(self, ctx: _IngestCtx) -> IngestResult:
        session_with_tenant = _core.session_with_tenant
        any_embedded = ctx.any_embedded
        chunks = ctx.chunks
        chunks_to_embed = ctx.chunks_to_embed
        doc_id = ctx.doc_id
        is_reindex = ctx.is_reindex
        rows = ctx.rows
        stale_indices = ctx.stale_indices
        unchanged_indices = ctx.unchanged_indices
        workspace_id = ctx.workspace_id
        record_bot_id = ctx.record_bot_id
        record_tenant_id = ctx.record_tenant_id
        title = ctx.title
        _audit = ctx.audit
        _audit_bot = ctx.audit_bot
        _ingest_t0 = ctx.ingest_t0
        # Atomic state flip + progress=100 in ONE transaction.
        # 2 bugs gây 4 doc stuck DRAFT 25+ phút trong prod 2026-05-13:
        # Bug B: KHÔNG có code flip state='active' sau worker complete.
        # Bug A: log "ingested" SUCCESS dù chunks_null_embedding > 0.
        # Bug MED #6: 2 separate UPDATE (progress + state) tạo observable
        #   inconsistency window khi UI poll giữa 2 tx.
        # Fix gộp: SELECT count + COUNT(embedding) → decide active/failed
        # → UPDATE state + progress + chunks_processed atomic.
        try:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                # Parent-child mode (HDT) intentionally does NOT embed
                # parent chunks — only children. Parents are referenced
                # by `child.parent_chunk_id` FK and only used for context
                # expansion at retrieval. Counting parent NULL as fail
                # is wrong (regression introduced 2026-05-13).
                #
                # Correct check: count only non-parent chunks (chunks
                # NOT referenced as parent_chunk_id by any other row).
                _check = await session.execute(
                    text(
                        """
                        SELECT
                          COUNT(*) AS total,
                          COUNT(*) FILTER (
                            WHERE embedding IS NOT NULL
                          ) AS embedded,
                          COUNT(*) FILTER (
                            WHERE embedding IS NULL
                              AND NOT EXISTS (
                                SELECT 1 FROM document_chunks ch
                                WHERE ch.parent_chunk_id = document_chunks.id
                              )
                          ) AS null_non_parent
                        FROM document_chunks
                        WHERE record_document_id = :doc_id
                        """,
                    ),
                    {"doc_id": doc_id},
                )
                _row = _check.fetchone()
                _total = int(_row[0]) if _row else 0
                _embedded = int(_row[1]) if _row else 0
                _null_non_parent = int(_row[2]) if _row else 0
                if _total == 0:
                    # Fail-LOUD (audit 2026-06-13): zero persisted chunks is an
                    # ingest FAILURE, not a warning. ERROR level + state=failed
                    # so monitoring + the recovery sweep both see it; the doc is
                    # NOT advertised as ingested below (gated on _final_state).
                    logger.error(
                        "ingest_zero_chunks_persisted_failed",
                        document_id=str(doc_id), title=title,
                    )
                    _final_state = "failed"
                    _final_step = "failed"
                elif _null_non_parent > 0:
                    # Only fail if leaf (non-parent) chunk has NULL embed
                    # — parent chunks legitimately have NULL by design.
                    logger.warning(
                        "ingest_partial_embedding_marking_failed",
                        document_id=str(doc_id), title=title,
                        chunks_total=_total, chunks_embedded=_embedded,
                        chunks_null_leaf=_null_non_parent,
                    )
                    _final_state = "failed"
                    _final_step = "failed"
                else:
                    _final_state = "active"
                    _final_step = "active"
                await session.execute(
                    text(
                        """
                        UPDATE documents SET
                          state = :s,
                          current_step = :step,
                          progress_percent = 100,
                          chunks_processed = :cp,
                          progress_updated_at = now(),
                          updated_at = now()
                        WHERE id = :id
                        """,
                    ),
                    {
                        "s": _final_state,
                        "step": _final_step,
                        "cp": _total,
                        "id": doc_id,
                    },
                )
                await session.commit()
                _flip_committed = True
        except Exception as exc:  # noqa: BLE001 — state flip best-effort
            _flip_committed = False
            logger.error(
                "ingest_state_flip_failed",
                document_id=str(doc_id), title=title,
                error_type=type(exc).__name__, error=str(exc)[:200],
            )
        if _flip_committed:
            # Terminal flip bumped documents.updated_at → the derived
            # corpus_version hash changed; bust the Redis memo so the
            # very next turn sees the new corpus (no 300s TTL lag).
            await self._invalidate_corpus_version(
                record_tenant_id, record_bot_id,
            )

        # Only advertise the document as ingested when the terminal state flip
        # actually committed AND landed on ``active`` (audit 2026-06-13: the
        # log previously fired unconditionally — emitting ``document_ingested``
        # for docs that failed or whose flip threw, an observability lie that
        # forced recovery to filter on two contradictory signals).
        if _flip_committed and _final_state == "active":
            logger.info(
                "document_ingested",
                title=title,
                chunks=len(chunks),
                bot_id=str(record_bot_id),
                chunks_new=len(chunks_to_embed),
                chunks_unchanged=len(unchanged_indices),
                chunks_deleted=len(stale_indices),
            )
        else:
            logger.error(
                "document_ingest_failed",
                title=title,
                document_id=str(doc_id),
                final_state=_final_state,
                flip_committed=_flip_committed,
                chunks=len(chunks),
            )

        # GraphRAG entity extraction — async background task (non-blocking)
        # When graph_rag_lazy_mode is enabled, skip upfront extraction entirely;
        # graph traversal will happen at query time instead (LazyGraphRAG).
        _lazy_mode = False
        if self._cfg is not None:
            _lazy_mode = await self._cfg.get_bool("graph_rag_lazy_mode", False)

        if not _lazy_mode:
            def _graph_task_done(task: asyncio.Task) -> None:
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    logger.error(
                        "graph_entity_extraction_background_failed",
                        error=str(exc),
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

            _bg_task = asyncio.create_task(
                self._extract_graph_entities(
                    bot_uuid=record_bot_id,
                    title=title,
                    chunks_to_process=[(idx, txt) for idx, txt, _h in chunks_to_embed],
                    rows_inserted=rows,
                    record_tenant_id=record_tenant_id,
                )
            )
            _bg_task.add_done_callback(_graph_task_done)
        else:
            logger.info(
                "graph_entity_extraction_skipped_lazy_mode",
                bot_id=str(record_bot_id),
                title=title,
            )

        # Stats Index — extract entities from table chunks (deterministic Python,
        # no LLM).  Re-ingest path deletes stale rows BEFORE inserting new ones
        # so queries never see a mix of old + new entities for the same document.
        # Both operations are best-effort (failures logged, not re-raised) so a
        # stats-index outage cannot block the ingest pipeline.
        if self._stats_index_repo is not None and rows:
            from ragbot.shared.document_stats import (  # noqa: PLC0415
                aggregate_summary,
                parse_table_chunks,
            )
            _stats_entities = parse_table_chunks(rows)
            if _stats_entities:
                if is_reindex:
                    # Delete stale stats rows before inserting updated ones.
                    try:
                        await self._stats_index_repo.delete_by_document(doc_id)
                    except Exception as exc:  # noqa: BLE001 — delete is best-effort
                        logger.warning(
                            "stats_index_delete_before_reingest_failed",
                            record_document_id=str(doc_id),
                            error_type=type(exc).__name__,
                            error=str(exc)[:200],
                        )
                _ws = workspace_id or (
                    str(record_tenant_id) if record_tenant_id else "system"
                )
                await self._insert_stats_index(
                    record_tenant_id=record_tenant_id or uuid.uuid4(),
                    workspace_id=_ws,
                    record_bot_id=record_bot_id,
                    record_document_id=doc_id,
                    entities=_stats_entities,
                )
                _summary = aggregate_summary(_stats_entities)
                await self._upsert_doc_summary(
                    record_document_id=doc_id,
                    summary_json=_summary,
                )

        if _audit is not None:
            _avg_chunk_len = (
                sum(len(c) for c in chunks) // len(chunks) if chunks else 0
            )
            _duration_ms = int(
                (time.perf_counter() - _ingest_t0) * 1000
            )
            await _audit.log(
                _audit_bot,
                "ingest",
                "ingest_completed",
                {
                    "document_id": str(doc_id),
                    "title": title,
                    "total_chunks": len(chunks),
                    "chunks_new": len(chunks_to_embed),
                    "chunks_unchanged": len(unchanged_indices),
                    "chunks_deleted": len(stale_indices),
                    "avg_chunk_len": _avg_chunk_len,
                    "embedded": bool(any_embedded),
                    "duration_ms": _duration_ms,
                    "is_reindex": is_reindex,
                },
            )

        return IngestResult(
            document_id=doc_id,
            title=title,
            chunks=len(chunks),
            embedded=any_embedded or (len(unchanged_indices) > 0 and is_reindex),
            chunks_new=len(chunks_to_embed),
            chunks_unchanged=len(unchanged_indices),
            chunks_deleted=len(stale_indices),
        )
