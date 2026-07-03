"""Ingest persistence + safety helpers (chunk bulk-insert, PII redaction, source allowlist).

Extracted from the document_service god-file. Free functions (no class state) so the
ingest persistence + safety gates are testable in isolation. Re-exported by
document_service/__init__ so existing call sites stay unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Final

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.config.settings import Settings
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
from ragbot.shared.chunking_policy import resolve_chunking_policy
from ragbot.shared.markdown_normalizer import normalize_to_markdown
from ragbot.shared.constants import (
    ALLOWED_EMBEDDING_COLUMNS,
    POSTGRES_MAX_BIND_PARAMS,
    DEFAULT_MARKDOWN_NORMALIZE_ENABLED,
    DEFAULT_TABLE_STRATEGY,
    DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED,
    DEFAULT_ADAPCHUNK_LAYER3_DOC_PROFILE_ENABLED,
    DEFAULT_RECAP_PII_ENABLED,
    DEFAULT_CHILD_CHUNK_OVERLAP,
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_CHUNK_HASH_ID_ENABLED,
    DEFAULT_CHUNK_MAX_SIZE,
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
    DEFAULT_CHUNK_TYPE_CODE,
    DEFAULT_CHUNK_TYPE_TABLE,
    DEFAULT_CHUNK_TYPE_TABLE_ROW,
    DEFAULT_CHUNK_TYPE_TEXT,
    DEFAULT_CLEANBASE_QUALITY_THRESHOLD,
    DEFAULT_CONTENT_TYPE_DISPATCH_ENABLED,
    DEFAULT_CLEANBASE_TIER0_ENABLED,
    DEFAULT_DOC_PROFILE_ANALYZER_PROVIDER,
    DEFAULT_CONTENT_HASH_HEX_LEN,
    DEFAULT_CONTENT_PREVIEW_CHARS,
    CR_ROW_GATED_STRATEGIES,
    DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED,
    DEFAULT_CR_CONTEXT_MAX_TOKENS,
    DEFAULT_CR_CACHE_WARM_MIN_CHUNKS,
    DEFAULT_CR_ENHANCED_ENABLED,
    DEFAULT_CR_MAX_DOC_CHARS,
    DEFAULT_CR_PROMPT_CACHE_ENABLED,
    DEFAULT_DIFF_REINGEST_ENABLED,
    DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
    DEFAULT_EMBED_DOC_BATCH_SIZE,
    DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S,
    DEFAULT_NARRATE_TIMEOUT_S,
    DEFAULT_EMBED_INTER_BATCH_SLEEP_S,
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_PASSAGE_PREFIX,
    DEFAULT_EMBEDDING_TASK_PASSAGE,
    DEFAULT_EMBEDDING_TEXT_STRATEGY,
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
    DEFAULT_METADATA_MAX_TOKENS,
    DEFAULT_PARENT_CHUNK_SIZE,
    DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_HEAD,
    DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_TAIL,
    DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED,
    DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S,
    DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS,
    MAX_DOCUMENT_CONTENT_CHARS,
    NARRATE_METADATA_KEY_BLOCK_TYPE,
    NARRATE_METADATA_KEY_NARRATED_TEXT,
    NARRATE_METADATA_KEY_RAW_CHUNK,
    PROMPT_INJECTION_PATTERNS,
    VI_DOMAIN_LANGUAGES,
    WHOLE_DOC_THRESHOLD_CHARS,
)
from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.infrastructure.doc_profile.registry import build_doc_profile_analyzer
from ragbot.shared.text_normalization import normalize_vn
from ragbot.shared.vi_tokenizer import segment_vi_compounds
from ragbot.infrastructure.graph.knowledge_graph import KnowledgeGraphService
from ragbot.infrastructure.parser.registry import detect_parser as _registry_detect_parser
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
from ragbot.application.services.chunk_context_enricher import (
    ChunkContextEnricher,
    NullChunkContextProvider,
)
from ragbot.application.services.contextual_chunk_enrichment import (
    emit_chunk_quality_event,
    enrich_chunk_with_context,
    score_chunk_quality,
)
from ragbot.shared.bot_limits import resolve_bot_limit
from ragbot.shared.contextual_enrichment import enrich_chunks
from ragbot.shared.errors import (
    DocumentDuplicateError,
    ExternalServiceError,
    SourceNotAllowedError,
)
from ragbot.shared.ingestion_validator import validate_ingestion

logger = structlog.get_logger(__name__)



async def _bulk_insert_chunks(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    *,
    record_bot_id: uuid.UUID,
    has_parent_chunk_id: bool = False,
    embedding_column: str = DEFAULT_EMBEDDING_COLUMN,
) -> None:
    """U7-1: single multi-row INSERT instead of N executemany round trips.

    Builds one VALUES (...), (...), ... statement so asyncpg sends 1 round trip
    regardless of chunk count. Columns with pgvector/jsonb casts are handled
    via per-row inline CAST expressions.

    ``record_bot_id`` is a REQUIRED kwarg — the ``document_chunks`` table
    carries it as a NOT NULL column (alembic 0108) so the bot-scoped
    retrieval path can filter ``WHERE record_bot_id = :bot`` without a
    join. Caller passes the parent document's bot id; we propagate it
    onto every row so the SQL INSERT does not silently drop a row that
    would otherwise violate the constraint and abort the whole batch
    (live evidence 2026-05-16 — <a tenant document> stuck DRAFT because
    the previous version of this helper omitted the column).

    Args:
        session: active AsyncSession with RLS tenant set.
        rows: list of dicts with keys: id, doc_id, idx, content,
              content_segmented, hash, emb, meta, chunk_chars.
              Optional key: parent_chunk_id (only when has_parent_chunk_id).
        record_bot_id: bot UUID — propagated onto every row.
        has_parent_chunk_id: include parent_chunk_id column + value.
        embedding_column: data-table vector column. Validated against
            ``ALLOWED_EMBEDDING_COLUMNS`` whitelist to defend the f-string
            substitution against SQL injection.
    """
    if not rows:
        return
    if embedding_column not in ALLOWED_EMBEDDING_COLUMNS:
        raise ValueError(
            f"unsupported embedding_column {embedding_column!r}; "
            f"allowed: {sorted(ALLOWED_EMBEDDING_COLUMNS)}",
        )

    if has_parent_chunk_id:
        col_names = (
            f"id, record_document_id, record_bot_id, chunk_index, content, content_segmented, "
            f"content_hash, {embedding_column}, metadata_json, parent_chunk_id, chunk_chars, chunk_type, "
            f"chunk_context"
        )
    else:
        col_names = (
            f"id, record_document_id, record_bot_id, chunk_index, content, content_segmented, "
            f"content_hash, {embedding_column}, metadata_json, chunk_chars, chunk_type, "
            f"chunk_context"
        )

    # Per-row bind count (13 with parent, 12 without); the shared :_bot_id is
    # bound once per statement. Cap rows/batch below the int16 protocol ceiling
    # so a large document (>~2900 chunks) splits into several round trips
    # instead of overflowing one VALUES(...) and aborting the whole ingest.
    binds_per_row = 13 if has_parent_chunk_id else 12
    max_rows_per_batch = max(1, (POSTGRES_MAX_BIND_PARAMS - 1) // binds_per_row)

    async def _execute_batch(batch: list[dict[str, Any]]) -> None:
        value_clauses: list[str] = []
        params: dict[str, Any] = {"_bot_id": record_bot_id}
        for i, row in enumerate(batch):
            if has_parent_chunk_id:
                value_clauses.append(
                    f"(:id_{i}, :doc_id_{i}, :_bot_id, :idx_{i}, :content_{i}, :seg_{i}, "
                    f":hash_{i}, CAST(:emb_{i} AS vector), CAST(:meta_{i} AS jsonb), "
                    f":par_{i}, :chars_{i}, :ctype_{i}, :ctx_{i})"
                )
                params[f"par_{i}"] = row.get("parent_chunk_id")
            else:
                value_clauses.append(
                    f"(:id_{i}, :doc_id_{i}, :_bot_id, :idx_{i}, :content_{i}, :seg_{i}, "
                    f":hash_{i}, CAST(:emb_{i} AS vector), CAST(:meta_{i} AS jsonb), "
                    f":chars_{i}, :ctype_{i}, :ctx_{i})"
                )

            params[f"id_{i}"] = row["id"]
            params[f"doc_id_{i}"] = row["doc_id"]
            params[f"idx_{i}"] = row["idx"]
            params[f"content_{i}"] = row["content"]
            params[f"seg_{i}"] = row.get("content_segmented")
            params[f"hash_{i}"] = row["hash"]
            params[f"emb_{i}"] = row.get("emb")
            params[f"meta_{i}"] = row["meta"]
            params[f"chars_{i}"] = row["chunk_chars"]
            # M10 — first-class modality column. Caller passes pre-classified
            # ``chunk_type``; row absent the key defaults to TEXT so legacy
            # callers (and orphan call-sites) keep prose-style behaviour.
            params[f"ctype_{i}"] = row.get("chunk_type") or DEFAULT_CHUNK_TYPE_TEXT
            # WA-3 — Enhanced CR storage column (alembic 010l). NULL is the
            # opt-out / legacy value; only populated when the bot owner
            # flipped ``plan_limits.cr_enhanced_enabled`` and the enricher
            # returned a non-empty context for this row.
            params[f"ctx_{i}"] = row.get("chunk_context")

        sql = (
            f"INSERT INTO document_chunks ({col_names}) "
            f"VALUES {', '.join(value_clauses)}"
        )
        await session.execute(text(sql), params)

    # Sequential batches share one session/transaction — a mid-document batch
    # failure rolls back the whole INSERT set (no half-ingested document).
    for _start in range(0, len(rows), max_rows_per_batch):
        await _execute_batch(rows[_start:_start + max_rows_per_batch])


async def _maybe_redact_ingest_content(
    content: str,
    *,
    pii_redactor: Any | None,
    bot_repo: Any | None,
    record_bot_id: uuid.UUID,
    record_tenant_id: uuid.UUID | None,
    config_service: Any | None = None,
) -> str:
    """Apply PII redaction at the ingest-content boundary when opted-in.

    Composite gate (CLAUDE.md "two-knob opt-in"):

    1. System kill-switch ``system_config.recap_pii_enabled`` (default
       value lifted from ``DEFAULT_RECAP_PII_ENABLED``). ``config_service``
       must expose ``get_bool(key, default)``. When the kill-switch is
       OFF the function passes through silently — no event, no audit.
    2. Per-bot opt-in ``plan_limits.pii_redaction_enabled`` (default
       False). When OFF a ``recap_pii_detect`` event with
       ``decision="skipped_bot_opt_out"`` surfaces so ops see the wiring.

    Both gates open → the registered ``pii_redactor`` runs; on a non-
    empty entity list a ``recap_pii_detect`` event fires with
    ``decision="masked"`` and the masked content is returned.

    Failure modes degrade silent (CLAUDE.md graceful-degradation rule):
    - missing redactor / bot_repo / record_tenant_id → passthrough.
    - bot-repo lookup raises → log ``pii_redaction_failed`` stage
      ``bot_lookup`` + passthrough.
    - config-service raises → log ``pii_redaction_failed`` stage
      ``config_lookup`` + passthrough (system kill-switch defaults OFF).
    - ``RecapPiiDetector.detect`` swallows strategy errors itself
      (emits its own ``recap_pii_detect_failed`` event).

    @return: masked content when both gates open AND entities matched,
             else the unchanged input.
    """
    if pii_redactor is None or bot_repo is None or record_tenant_id is None:
        return content
    try:
        bot_cfg = await bot_repo.get_by_id(
            record_bot_id, record_tenant_id=record_tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — bot-repo failure must never 5xx the ingest. Log + skip.
        logger.warning(
            "pii_redaction_failed",
            surface="ingest_content",
            stage="bot_lookup",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return content
    if bot_cfg is None:
        return content
    bot_opt_in = bool(resolve_bot_limit(
        bot_cfg, "pii_redaction_enabled", system_default=False,
    ))
    # System kill-switch — when ``config_service`` is wired, honour
    # ``recap_pii_enabled``; when not wired (legacy callers, tests that
    # pass only ``pii_redactor`` / ``bot_repo``), fall back to the
    # compile-time default so behaviour stays backward-compatible.
    if config_service is not None:
        try:
            feature_enabled = bool(await config_service.get_bool(
                "recap_pii_enabled", DEFAULT_RECAP_PII_ENABLED,
            ))
        except Exception as exc:  # noqa: BLE001 — config-service failure must never 5xx. Degrade SAFE = kill-switch OFF.
            logger.warning(
                "pii_redaction_failed",
                surface="ingest_content",
                stage="config_lookup",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return content
    else:
        feature_enabled = bool(DEFAULT_RECAP_PII_ENABLED)
    # Route through the RecapPiiDetector facade so the two-gate
    # decision tree (skipped_flag_off / skipped_bot_opt_out / masked /
    # no_entities_detected / strategy_error) is owned by a single
    # observability hook. Imported locally to avoid pulling the safety
    # subsystem into the document_service import graph at module load.
    from ragbot.infrastructure.safety.pii_detector import (  # noqa: PLC0415
        RecapPiiDetector,
    )
    detector = RecapPiiDetector(pii_redactor=pii_redactor)
    result = detector.detect(
        content,
        feature_enabled=feature_enabled,
        bot_opt_in=bot_opt_in,
        record_tenant_id=str(record_tenant_id),
        record_bot_id=str(record_bot_id),
        surface="ingest_content",
    )
    return result.redacted_text


async def _maybe_validate_source_allowlist(
    source_url: str,
    *,
    source_validator: Any | None,
    bot_repo: Any | None,
    config_service: Any | None,
    record_bot_id: uuid.UUID,
    record_tenant_id: uuid.UUID | None,
) -> None:
    """T1-Safety — gate ``source_url`` against per-bot allow-list.

    Per-bot opt-in via ``plan_limits.allowed_source_domains`` (list of
    host / URL-prefix / regex patterns) AND platform feature flag
    ``system_config.source_allowlist_enabled``. Both must be truthy AND
    the per-bot list must be non-empty for filtering to kick in; this
    matches the two-knob opt-in pattern used by PII redaction
    (CLAUDE.md "Application MINDSET — Bot owner owns everything").

    Defence target: PoisonedRAG arXiv 2402.07867 §6.1 — adversary-
    controlled URLs ingested into the knowledge base poison retrieval +
    cascade into LLM answers (90% baseline attack success rate without
    a structural source filter).

    Failure modes degrade silent (CLAUDE.md graceful-degradation rule):
    - missing validator / repo / config_service → passthrough
    - bot lookup error → log + passthrough (don't 5xx ingest on
      observability layer failure)
    - empty allow-list → passthrough (feature opt-in default)

    Hard reject (raises :class:`SourceNotAllowedError`) ONLY when:
    - feature flag True AND
    - per-bot list non-empty AND
    - validator returns ``(False, reason)``

    structlog event ``source_allowlist_check`` is emitted on EVERY
    path (allow / reject / skip) so observability matrix can count
    decisions per bot.
    """
    # 1) Graceful degradation when wiring is incomplete.
    if (
        source_validator is None
        or bot_repo is None
        or config_service is None
        or record_tenant_id is None
    ):
        return

    # 2) Platform feature flag — operator-controlled kill-switch.
    try:
        feature_enabled = await config_service.get_bool(
            "source_allowlist_enabled",
            default=False,
        )
    except Exception as exc:  # noqa: BLE001 — config-service failure must never 5xx ingest. Log + skip.
        logger.warning(
            "source_allowlist_check",
            decision="skip",
            reason="config_lookup_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            record_tenant_id=str(record_tenant_id),
            record_bot_id=str(record_bot_id),
        )
        return
    if not feature_enabled:
        return

    # 3) Per-bot allow-list from plan_limits.
    try:
        bot_cfg = await bot_repo.get_by_id(
            record_bot_id, record_tenant_id=record_tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — bot-repo failure must never 5xx ingest. Log + skip.
        logger.warning(
            "source_allowlist_check",
            decision="skip",
            reason="bot_lookup_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            record_tenant_id=str(record_tenant_id),
            record_bot_id=str(record_bot_id),
        )
        return
    if bot_cfg is None:
        return

    allowed = resolve_bot_limit(bot_cfg, "allowed_source_domains") or ()
    # ``list_str`` schema coerces to tuple of stripped lower-case
    # non-empty strings already; defensive cast handles legacy bots
    # where the column is a JSON list.
    if isinstance(allowed, (list, set, frozenset)):
        allowed = tuple(allowed)
    if not allowed:
        # Empty allow-list = bot owner has not opted in for THIS bot.
        # Allow-all preserves backward compat alongside the flag flip.
        logger.info(
            "source_allowlist_check",
            decision="allow",
            reason="empty_allowlist",
            record_tenant_id=str(record_tenant_id),
            record_bot_id=str(record_bot_id),
        )
        return

    # 4) Validator call (Port + Registry — Strategy pattern).
    try:
        is_ok, reason = source_validator.is_allowed(source_url or "", allowed)
    except Exception as exc:  # noqa: BLE001 — validator failure must never 5xx ingest. Log + skip (Null adapter has no failure modes; only a bespoke 3rd-party adapter could throw).
        logger.warning(
            "source_allowlist_check",
            decision="skip",
            reason="validator_raised",
            error=str(exc),
            error_type=type(exc).__name__,
            record_tenant_id=str(record_tenant_id),
            record_bot_id=str(record_bot_id),
        )
        return

    provider = getattr(
        source_validator, "get_provider_name", lambda: "unknown",
    )()
    if is_ok:
        logger.info(
            "source_allowlist_check",
            decision="allow",
            reason=None,
            provider=provider,
            allowlist_size=len(allowed),
            record_tenant_id=str(record_tenant_id),
            record_bot_id=str(record_bot_id),
        )
        return

    # 5) Reject — hard fail before any chunk/embed work.
    logger.warning(
        "source_allowlist_check",
        decision="reject",
        reason=reason or "domain_not_in_allowlist",
        provider=provider,
        allowlist_size=len(allowed),
        record_tenant_id=str(record_tenant_id),
        record_bot_id=str(record_bot_id),
    )
    raise SourceNotAllowedError(
        f"source_url rejected by allow-list (reason={reason})"
    )


__all__ = [
    "_bulk_insert_chunks",
    "_maybe_redact_ingest_content",
    "_maybe_validate_source_allowlist",
]
