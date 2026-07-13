"""Ingest finalize stage — split from ingest_stages to keep files navigable."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

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
    DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE,
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
from ragbot.shared.document_stats import ParsedEntity, _normalise


def _entity_richness(entity: ParsedEntity) -> tuple[int, int, int]:
    """Richness rank of an entity, larger = richer (more information).

    Used to pick which member of a duplicate group to KEEP. A duplicate group
    shares the same ``(normalised name, price_primary)`` key, so the members
    differ only in the OPTIONAL fields a group/summary chunk may have lost:
    a secondary price, extra attribute columns, a forward-filled category.
    Returns a sortable tuple — purely structural (None-checks + ``len``),
    domain-neutral, no corpus literal.
    """
    return (
        1 if entity.price_secondary is not None else 0,
        len(entity.attributes) if entity.attributes else 0,
        1 if entity.category else 0,
    )


def _stats_rows_for_document(
    chunks: list | None, rows: list[dict],
) -> list[dict]:
    """Rows the stats-index rebuild must parse — the document's FULL chunk set.

    On a re-ingest, ``rows`` holds only the chunks whose hash CHANGED (the store
    stage builds them from ``chunks_to_embed``). But the rebuild first calls
    ``delete_by_document``, which wipes EVERY index row for the document — so
    re-inserting from ``rows`` silently erases every entity that lives in an
    unchanged chunk (edit 3 chunks of a 500-chunk catalog and 497 entities vanish
    from the stats/SQL route while their vectors survive).

    ``chunks`` is the chunker's full, ordered, pre-enrichment output, so feeding it
    also keeps ``chunk_index`` correct — ``parse_table_chunks`` derives that from
    list POSITION, and handing it only the changed rows mis-numbered every entity.

    Falls back to ``rows`` when no chunk list is available (legacy/edge caller).
    """
    if not chunks:
        return rows
    out: list[dict] = []
    for _c in chunks:
        text = _c.get("content", "") if isinstance(_c, dict) else str(_c or "")
        out.append({"content": text})
    return out


def _dedup_stats_entities(
    entities: list[ParsedEntity],
) -> list[ParsedEntity]:
    """Collapse the duplicate entity rows the dual-index chunker creates.

    ``table_dual_index`` (and the header/footer synthetic chunks) emit each data
    row BOTH as its own single-row chunk AND inside a multi-row group/summary
    chunk, so ``parse_table_chunks`` extracts the same logical row several times
    — bloating ``document_service_index`` (a single product seen 5-16×) and
    over-counting every count/aggregate query. Collapse them here, BEFORE insert.

    Two-stage, deterministic (input order preserved; no dict-order / RNG reliance):

    1. **Exact-row collapse** — group by ``(normalised name, price_primary)`` so
       two genuinely-distinct services that share a name but carry DIFFERENT
       prices both survive (price is in the key); identical rows collapse to the
       RICHEST member (``_entity_richness``) — a per-row chunk that kept a
       secondary price / extra attributes beats the stripped group-chunk copy.
    2. **Priced-beats-unpriced** — when the SAME name has at least one priced
       survivor, drop its NULL-price survivor: a row whose price cell was empty
       in a summary chunk is the same logical entity as the priced row, not a
       second product. A name with ONLY null-price survivors keeps its single
       row (a genuinely price-less catalog entry stays searchable).

    Domain-neutral · zero-hardcode (no threshold) · pure function.
    """
    if not entities:
        return entities

    # Stage 1 — collapse exact (name, price_primary) duplicates, keep richest.
    # ``best`` preserves FIRST-seen insertion order (Python dict) so the output
    # order is a stable function of the input order, never dict hashing.
    best: dict[tuple[str, int | None], ParsedEntity] = {}
    for entity in entities:
        key = (_normalise(entity.name), entity.price_primary)
        incumbent = best.get(key)
        if incumbent is None or _entity_richness(entity) > _entity_richness(incumbent):
            best[key] = entity

    # Stage 2 — for each name that has a priced survivor, drop its null-price dup.
    names_with_price: set[str] = {
        name for (name, price) in best if price is not None
    }
    return [
        entity
        for (name, price), entity in best.items()
        if price is not None or name not in names_with_price
    ]


def _decide_ingest_state(
    total: int,
    embedded: int,
    null_non_parent: int,
    *,
    min_leaf_coverage: float,
) -> str:
    """Pure finalize decision → ``"active"`` | ``"failed"`` from chunk counts.

    Resilience (2026-06-20): the readiness gate serves only ``state='active'``
    and the recovery sweep does NOT re-process ``'failed'`` — so the old
    fail-on-ANY-null-leaf policy turned a single TRANSIENT embed miss (a provider
    429 on one batch) into PERMANENT dark on a 1/500 doc. Serve when leaf-embed
    coverage (embedded leaves / all leaves) is at/above the floor — the null
    leaves keep BM25 retrievability. Only a genuinely broken doc fails:

    - zero chunks persisted, or nothing embedded at all → ``failed``
    - all leaves embedded → ``active``
    - some null leaves but coverage >= floor → ``active`` (serve degraded)
    - coverage below floor → ``failed`` (re-ingest needed)
    """
    if total <= 0 or embedded <= 0:
        return "failed"
    if null_non_parent <= 0:
        return "active"
    leaf_total = embedded + null_non_parent
    coverage = embedded / leaf_total if leaf_total else 0.0
    return "active" if coverage >= min_leaf_coverage else "failed"


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
                # Resilience floor (config-overridable): serve a doc that is
                # MOSTLY embedded rather than taking the bot dark on a transient
                # 1/500 embed miss. See ``_decide_ingest_state``.
                _min_cov = DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE
                if self._cfg is not None:
                    try:
                        _min_cov = float(await self._cfg.get(
                            "ingest_min_leaf_embed_coverage",
                            DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE,
                        ))
                    except (ValueError, TypeError):
                        _min_cov = DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE
                _final_state = _decide_ingest_state(
                    _total, _embedded, _null_non_parent, min_leaf_coverage=_min_cov,
                )
                _final_step = _final_state
                _leaf_total = _embedded + _null_non_parent
                _cov = round(_embedded / _leaf_total, 4) if _leaf_total else 0.0
                if _total == 0 or _embedded == 0:
                    # Fail-LOUD (audit 2026-06-13): zero persisted chunks / nothing
                    # embedded is an ingest FAILURE. ERROR so monitoring + recovery
                    # sweep both see it; doc NOT advertised as ingested below.
                    logger.error(
                        "ingest_zero_chunks_persisted_failed",
                        document_id=str(doc_id), title=title,
                        chunks_total=_total, chunks_embedded=_embedded,
                    )
                elif _null_non_parent > 0 and _final_state == "active":
                    # Partial embed but coverage >= floor → SERVE degraded (null
                    # leaves keep BM25; a recovery re-embed can fill them later).
                    logger.warning(
                        "ingest_partial_embedding_serving_degraded",
                        document_id=str(doc_id), title=title,
                        chunks_total=_total, chunks_embedded=_embedded,
                        chunks_null_leaf=_null_non_parent,
                        leaf_coverage=_cov, min_leaf_coverage=_min_cov,
                    )
                elif _null_non_parent > 0:
                    # Coverage below floor → genuinely broken, re-ingest needed.
                    logger.warning(
                        "ingest_partial_embedding_marking_failed",
                        document_id=str(doc_id), title=title,
                        chunks_total=_total, chunks_embedded=_embedded,
                        chunks_null_leaf=_null_non_parent,
                        leaf_coverage=_cov, min_leaf_coverage=_min_cov,
                    )
                await session.execute(
                    text(
                        """
                        UPDATE documents SET
                          state = :s,
                          current_step = :step,
                          progress_percent = 100,
                          chunks_processed = :cp,
                          -- A doc that just (re)ingested to ``active`` is LIVE,
                          -- not deleted: clear any stale soft-delete flag so a
                          -- re-ingest of a previously-deleted doc_id is visible
                          -- again (it was being soft-deleted + reactivated and
                          -- left invisible to the deleted_at IS NULL doc count).
                          -- ``:clear_deleted`` is a Python bool (NOT ``:s =
                          -- 'active'``): binding the same ``:s`` param in both a
                          -- varchar assignment and a text comparison makes
                          -- asyncpg fail to deduce one type (AmbiguousParameter).
                          deleted_at = CASE WHEN :clear_deleted THEN NULL
                                            ELSE deleted_at END,
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
                        "clear_deleted": _final_state == "active",
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
            # Feed RAW pre-enrichment row text to the extractor — a narrate/CR
            # prefix in ``content`` ("Đoạn X nằm trong phần…") would otherwise be
            # parsed as a noise entity. The raw row text lives in each row's
            # ``meta.raw_chunk`` (set when the chunk was enrichment-persisted).
            def _raw_row(_r: dict) -> dict:
                _m = _r.get("meta")
                if _m:
                    try:
                        _parsed = json.loads(_m) if isinstance(_m, str) else _m
                        _raw = _parsed.get("raw_chunk")
                        if _raw:
                            return {**_r, "raw_chunk": _raw}
                    except (ValueError, TypeError, AttributeError):
                        pass
                return _r
            # ADR-0006 Tier 2: per-bot owner-declared column roles are AUTHORITATIVE
            # over header inference. The engine stays domain-neutral — it reads
            # ``custom_vocabulary["column_roles"]`` (e.g. {"RAM": "attribute"}) but
            # never hardcodes domain column meanings. Best-effort: a bot-repo failure
            # must never block ingest, so fall back to inference-only on any error.
            _custom_roles: dict[str, str] | None = None
            # A4 (ADR-0008): per-bot opt-in to pick the NAME column by value-shape
            # at ingest (headerless/uninferred tables) instead of the positional
            # first cell. Same plan_limits flag as the serve-time path so a bot that
            # opts in gets a correct entity_name at the SOURCE, not just a serve-time
            # patch. Default OFF → byte-identical legacy ingest.
            _name_by_shape = False
            if self._bot_repo is not None:
                try:
                    _bot_cfg = await self._bot_repo.get_by_id(
                        record_bot_id, record_tenant_id=record_tenant_id,
                    )
                    _vocab = getattr(_bot_cfg, "custom_vocabulary", None) or {}
                    _declared = _vocab.get("column_roles")
                    if isinstance(_declared, dict) and _declared:
                        _custom_roles = _declared
                    _name_by_shape = bool(
                        (getattr(_bot_cfg, "plan_limits", None) or {}).get(
                            "stats_name_by_shape", False
                        )
                    )
                except (SQLAlchemyError, ValueError, TypeError, AttributeError) as exc:
                    # Inference-only fallback; a bot-config lookup/shape error must
                    # never block ingest (DB error, config drift) — narrow per policy.
                    logger.warning(
                        "stats_index_custom_roles_lookup_failed",
                        record_document_id=str(doc_id),
                        error_type=type(exc).__name__,
                        error=str(exc)[:200],
                    )
            # Rebuild from the document's FULL chunk set, not just the chunks this
            # run re-embedded — ``delete_by_document`` below wipes every row for the
            # doc, so parsing only ``rows`` (the CHANGED chunks on a re-ingest)
            # silently erased every entity in an unchanged chunk. See
            # ``_stats_rows_for_document``.
            _stats_rows = _stats_rows_for_document(
                chunks, [_raw_row(_r) for _r in rows],
            )
            _raw_entities = parse_table_chunks(
                _stats_rows, _custom_roles, name_by_shape=_name_by_shape
            )
            # G4 data-quality ADVISORY (ADR-0005 — advisory, NEVER blocking). Surface
            # to the owner WHY coverage may be limited: a table with no resolvable
            # NAME column (entities can't be name-keyed) and/or header columns that
            # bound no role (searchable only as a generic attribute). The owner can
            # then declare ``custom_vocabulary["column_roles"]``. Domain-neutral: it
            # reports the owner's own header labels back; it does NOT drop data, does
            # NOT change state, and a failure here must never break ingest.
            try:
                from ragbot.shared.document_stats import (  # noqa: PLC0415
                    analyze_table_headers,
                )
                _dq = analyze_table_headers(_stats_rows, _custom_roles)
                if _dq["tables_seen"] and (
                    not _dq["has_name_column"] or _dq["unassigned_columns"]
                ):
                    logger.warning(
                        "ingest_data_quality",
                        record_document_id=str(doc_id),
                        record_bot_id=str(record_bot_id),
                        tables_seen=_dq["tables_seen"],
                        has_name_column=_dq["has_name_column"],
                        unassigned_columns=_dq["unassigned_columns"][:20],
                        advice="declare column_roles in custom_vocabulary",
                    )
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                # Advisory is best-effort observability — never block ingest.
                logger.warning(
                    "ingest_data_quality_failed",
                    record_document_id=str(doc_id),
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )
            # Dual-index emits each row in BOTH its own chunk AND a group/summary
            # chunk, so the same logical entity is extracted many times. Collapse
            # the duplicates BEFORE insert so the index holds one row per real
            # service/product (and count/aggregate queries stop over-counting).
            _stats_entities = _dedup_stats_entities(_raw_entities)
            if _raw_entities:
                logger.info(
                    "stats_index_dedup",
                    record_document_id=str(doc_id),
                    entities_raw=len(_raw_entities),
                    entities_deduped=len(_stats_entities),
                    collapsed=len(_raw_entities) - len(_stats_entities),
                )
            if _stats_entities:
                # Idempotent write: ALWAYS delete the document's existing stats
                # rows immediately before re-inserting the freshly-extracted set,
                # regardless of is_reindex. The ingest task is delivered
                # at-least-once (Redis Streams) so the same doc_id can be
                # processed more than once even on a first-time doc
                # (is_reindex=False); without an unconditional delete each retry
                # appends a full duplicate copy. On a brand-new doc the delete
                # removes 0 rows (cheap). If the delete fails we must NOT insert
                # — inserting on top of un-deleted rows is exactly what produces
                # the duplicates — so we skip this pass and let the next
                # successful ingest re-populate.
                _stats_delete_ok = True
                try:
                    await self._stats_index_repo.delete_by_document(
                        doc_id, record_bot_id=record_bot_id
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort; skip insert on fail
                    _stats_delete_ok = False
                    logger.warning(
                        "stats_index_delete_before_insert_failed",
                        record_document_id=str(doc_id),
                        error_type=type(exc).__name__,
                        error=str(exc)[:200],
                    )
                _ws = workspace_id or (
                    str(record_tenant_id) if record_tenant_id else "system"
                )
                if _stats_delete_ok and record_tenant_id is not None:
                    await self._insert_stats_index(
                        record_tenant_id=record_tenant_id,
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
                elif _stats_delete_ok:
                    # record_tenant_id is None — fail-loud, never fabricate a
                    # random tenant UUID. A fabricated tenant writes orphan
                    # stats rows under a UUID no query ever scopes to (tenant
                    # isolation is sacred); skip the index instead.
                    logger.warning(
                        "stats_index_insert_skipped_no_tenant",
                        record_document_id=str(doc_id),
                        record_bot_id=str(record_bot_id),
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
            strategy_used=ctx.strategy_used,
        )
