"""Phase-D ingest observability: canonical step names + per-phase wrappers.

Extracted from the document_service god-file. ``_phase_d_step`` wraps each ingest
U-phase to emit a request_steps row; ``_update_doc_progress`` advances the document
state machine. Re-exported by document_service/__init__.
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




# === Phase D ingest observability — canonical step names ===
# Each name maps 1-1 to a wrapped phase inside ``DocumentService.ingest()``.
# Order matches the runtime sequence so analytics can join on (step_order,
# step_name) deterministically. Defined here (not in ``shared/constants.py``)
# because ingest-specific constants are co-located with the only writer; if
# additional readers appear (analytics, dashboards) lift to constants.py.
INGEST_STEP_NAMES: Final[tuple[str, ...]] = (
    "ingest_validate",
    "ingest_parse",
    "ingest_clean",
    "ingest_chunk",
    "ingest_enrich",
    "ingest_vn_segment",
    "ingest_embed_store",
)
# Tracker namespace label injected into ``request_steps.metadata_json`` so
# analytics can split ingest vs query rows without a JOIN to ``request_logs``.
INGEST_STEP_KIND: Final[str] = "ingest"


@asynccontextmanager
async def _phase_d_step(
    tracker: Any | None,
    name: str,
) -> AsyncIterator[Any]:
    """Yield a step context — real ``StepTracker.step()`` or a no-op.

    Always tags ``metadata.step_kind = INGEST_STEP_KIND`` so persisted rows
    record their ingest-namespace origin without modifying the tracker
    contract. Backward-compat: ``tracker is None`` yields a minimal stub
    that swallows ``set_metadata`` / ``add_tokens`` calls so wrap sites
    stay flat regardless of whether a tracker was injected.
    """
    class _Noop:
        def set_metadata(self, **_kw: Any) -> None:
            return None

        def add_tokens(self, **_kw: Any) -> None:
            return None

    if tracker is None:
        yield _Noop()
        return

    # Phase-D observability: step_tracker is best-effort. If parent
    # request_log row doesn't exist (TenantIsolationViolation) or any
    # other observability failure, swallow + fall back to no-op so the
    # ingest job itself doesn't fail. The user-facing answer doesn't
    # depend on request_steps rows; analytics rows can rebuild later.
    #
    # `tracker.step()` may fail on either `__aenter__` (open) or
    # `__aexit__` (commit). We must yield exactly once regardless:
    #   - open fails           → yield Noop (already-failed path)
    #   - body runs OK + commit fails → swallow, do NOT re-yield
    # The 2-yield pitfall (RuntimeError: generator didn't stop) is what
    # the old try/except yield-twice form fell into.
    #
    # The outer ``try / except Exception`` MUST NOT swallow a body-side
    # exception that the wrap site explicitly re-raised; only failures
    # originating from the tracker (``tracker.step(...)`` construction
    # or its async-CM hooks) belong in the outer swallow path. We use
    # a sentinel to capture any body-propagated exception and re-raise
    # it AFTER the outer try has been cleared so it is not swallowed.
    _body_propagate: BaseException | None = None
    try:
        cm = tracker.step(name, metadata={"step_kind": INGEST_STEP_KIND})
        try:
            ctx = await cm.__aenter__()
        except Exception as exc:  # noqa: BLE001 — observability fail-soft on enter
            import structlog as _slog
            _slog.get_logger(__name__).warning(
                "ingest_step_tracker_open_swallow",
                step_name=name,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            yield _Noop()
            return
        try:
            yield ctx
        except BaseException as _body_exc:
            # Propagate caller-raised exception into tracker.__aexit__
            # so it records the failure, but swallow any commit-side
            # error from the tracker itself.
            import sys as _sys
            exc_type, exc_val, exc_tb = _sys.exc_info()
            try:
                suppress = await cm.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as cm_exc:  # noqa: BLE001 — observability fail-soft on exit
                import structlog as _slog
                _slog.get_logger(__name__).warning(
                    "ingest_step_tracker_exit_swallow",
                    step_name=name,
                    error_type=type(cm_exc).__name__,
                    error=str(cm_exc)[:200],
                )
                suppress = False
            if not suppress:
                _body_propagate = _body_exc
        else:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as cm_exc:  # noqa: BLE001 — observability fail-soft on exit
                import structlog as _slog
                _slog.get_logger(__name__).warning(
                    "ingest_step_tracker_commit_swallow",
                    step_name=name,
                    error_type=type(cm_exc).__name__,
                    error=str(cm_exc)[:200],
                )
    except Exception as exc:  # noqa: BLE001 — defensive outer catch
        import structlog as _slog
        _slog.get_logger(__name__).warning(
            "ingest_step_tracker_outer_swallow",
            step_name=name,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return
    if _body_propagate is not None:
        raise _body_propagate


@dataclass
class IngestResult:
    document_id: uuid.UUID
    title: str
    chunks: int
    embedded: bool
    chunks_new: int = 0
    chunks_unchanged: int = 0
    chunks_deleted: int = 0


async def _update_doc_progress(
    session_factory: Any,
    record_tenant_id: Any,
    document_id: Any,
    *,
    current_step: str,
    progress_percent: int,
    chunks_total: int | None = None,
    chunks_processed: int | None = None,
) -> None:
    """Update documents.{current_step,progress_percent,chunks_*} for UI poll.

    Best-effort: any DB error is swallowed (observability layer must
    never block ingest). UPDATE wraps a tenant-scoped session so RLS
    policies stay enforced.
    """
    try:
        from ragbot.infrastructure.db.engine import session_with_tenant  # noqa: PLC0415
        async with session_with_tenant(
            session_factory, record_tenant_id=record_tenant_id,
        ) as session:
            await session.execute(
                text(
                    """
                    UPDATE documents SET
                      current_step = :step,
                      progress_percent = :pct,
                      chunks_total = COALESCE(:ct, chunks_total),
                      chunks_processed = COALESCE(:cp, chunks_processed),
                      progress_updated_at = now()
                    WHERE id = :doc_id
                    """,
                ),
                {
                    "step": current_step,
                    "pct": max(0, min(100, int(progress_percent))),
                    "ct": chunks_total,
                    "cp": chunks_processed,
                    "doc_id": document_id,
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — observability fail-soft
        import structlog as _slog
        _slog.get_logger(__name__).debug(
            "doc_progress_update_swallow",
            document_id=str(document_id),
            step=current_step,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )


__all__ = [
    "IngestResult",
    "INGEST_STEP_NAMES",
    "INGEST_STEP_KIND",
    "_phase_d_step",
    "_update_doc_progress",
]
