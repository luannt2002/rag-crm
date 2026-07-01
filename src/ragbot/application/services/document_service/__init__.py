"""Document ingestion service — chunk, embed, and store.

Consolidates the duplicated chunk+embed+store logic into a single
reusable service so every ingest source shares one canonical code path.
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
    DEFAULT_EMBEDDING_MODEL_BY_LANGUAGE,
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
from ragbot.infrastructure.parser.registry import (
    detect_parser as _registry_detect_parser,
    detect_parser_robust,
)
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
# Observability helpers live in ingest_phases to keep this module focused.
from ragbot.application.services.document_service.ingest_phases import *  # noqa: E402,F401,F403
# Text cleaning + chunk-typing live in text_processing for the same reason.
from ragbot.application.services.document_service.text_processing import *  # noqa: E402,F401,F403
# Ingest persistence + safety helpers live in ingest_helpers for the same reason.
from ragbot.application.services.document_service.ingest_helpers import *  # noqa: E402,F401,F403
from ragbot.application.services.document_service.ingest_core import _IngestMixin  # noqa: E402


# ── Content-state purge: single source of truth ──────────────────────────────
# Every delete / re-ingest path purges the SAME set of content-state tables so no
# path can silently forget one (the re-ingest bug that left stale
# ``document_service_index`` col_N rows: chunk-purge was inline-duplicated but the
# stats-index purge was only in single-doc delete). Content is derived/rebuildable
# → HARD delete. Metadata (``documents``) is soft-deleted by the caller (forensic).
# Audit / cost tables (``audit_log`` / ``request_logs`` / ``request_steps``) are
# NEVER touched here — append-only forensic/billing. Add a content table = edit the
# tuple ONCE and every path purges it.
_CONTENT_TABLES: Final[tuple[str, ...]] = ("document_chunks", "document_service_index")


async def _purge_content_tables(session: Any, *, doc_filter: str, params: dict[str, Any]) -> int:
    """Hard-purge every content-state table for the documents matching *doc_filter*.

    ``doc_filter`` is a SQL predicate on the ``documents`` table supplied by the caller
    from a FIXED template (values are bound in *params*), so purge runs as ONE atomic
    ``DELETE … WHERE record_document_id IN (SELECT id FROM documents WHERE <filter>)``
    per table — no resolve round-trip, correct even when nothing matches. Table names
    come from the fixed ``_CONTENT_TABLES`` whitelist (never user input → injection-safe).
    Returns rows removed from ``document_chunks`` (logging parity).
    """
    chunks = 0
    for tbl in _CONTENT_TABLES:
        r = await session.execute(
            text(
                f"DELETE FROM {tbl} WHERE record_document_id IN "
                f"(SELECT id FROM documents WHERE {doc_filter})"
            ),
            params,
        )
        if tbl == "document_chunks":
            chunks = r.rowcount or 0
    return chunks


class DocumentService(_IngestMixin):
    """Chunk, embed, and store a bot's documents."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Any,
        settings: Settings,
        config_service: Any | None = None,
        audit_logger: Any | None = None,
        parser_detector: Any | None = None,
        model_resolver: Any | None = None,
        pii_redactor: Any | None = None,
        bot_repo: Any | None = None,
        source_validator: Any | None = None,
        chunk_context_enricher: ChunkContextEnricher | None = None,
        stats_index_repo: Any | None = None,
        narrate_service: Any | None = None,
        corpus_version_service: Any | None = None,
    ) -> None:
        """Initialize the service with its session factory, embedder, and settings.
        @param session_factory: factory that creates async DB sessions
        @param embedder: service that produces embedding vectors
        @param settings: application config (FALLBACK ONLY — system_config overrides)
        @param config_service: SystemConfigService for runtime config (primary source)
        @param audit_logger: optional PipelineAuditLogger for JSONL leader-trace
        @param parser_detector: callable ``(mime, ext) -> DocumentParserPort | None``
            for routing raw bytes to a registry parser. Defaults to
            :func:`ragbot.infrastructure.parser.registry.detect_parser`.
        @param model_resolver: ModelResolverService for per-bot embedding binding
            resolution — ingest path must honor ``bot_model_bindings`` so each
            bot's vectors are written under the dimensionality declared in its
            binding metadata.
        @param pii_redactor: optional PiiRedactorPort. When provided AND the
            bot has ``plan_limits.pii_redaction_enabled=True``, raw document
            content is masked at the ingest boundary before chunking +
            persist. ``None`` (or a NullPiiRedactor) = passthrough so
            existing tenants see no behaviour change.
        @param bot_repo: optional BotRepository used to look up the per-bot
            ``plan_limits`` toggle. ``None`` = skip redaction entirely so
            test wiring without a repo still works.
        @param stats_index_repo: optional StatsIndexRepository. When provided,
            table-typed chunks are parsed for numeric entities and written to
            ``document_service_index``. ``None`` = feature wired off (passthrough).
        """
        self._sf = session_factory
        self._embedder = embedder
        self._settings = settings
        self._cfg = config_service
        self._audit = audit_logger
        self._parser_detector = parser_detector or _registry_detect_parser
        self._model_resolver = model_resolver
        self._pii_redactor = pii_redactor
        self._bot_repo = bot_repo
        # Source-URL allow-list validator (Strategy registry from
        # ragbot.infrastructure.safety). ``None`` (default) means the
        # feature is wired off → ``_maybe_validate_source_allowlist``
        # degrades to passthrough. Worker / bootstrap inject the real
        # validator best-effort via the DI container hook.
        self._source_validator = source_validator
        # Enhanced Contextual Retrieval enricher (Port + DI). When
        # ``None`` the ingest path builds a default ``ChunkContextEnricher``
        # wrapping ``NullChunkContextProvider`` so the column stays empty
        # until DI wires a real Haiku-batch provider. Per-bot opt-in is
        # gated separately via ``plan_limits.cr_enhanced_enabled``.
        self._chunk_context_enricher = (
            chunk_context_enricher
            if chunk_context_enricher is not None
            else ChunkContextEnricher(provider=NullChunkContextProvider())
        )
        # Stats Index — deterministic numeric entity extraction from table
        # chunks. ``None`` means the feature is wired off; existing callers
        # that do not pass the repo see passthrough with zero behaviour change.
        self._stats_index_repo = stats_index_repo
        # Narrate-then-Embed: when wired, LaTeX/TABLE chunks pass through
        # the narrator → natural-language text before embed. ``None`` keeps
        # the non-narrated behaviour (embed raw chunk bytes).
        self._narrate_service = narrate_service
        # Corpus-version bust on every corpus mutation (ingest terminal
        # flip + doc-delete family) so the semantic-cache key rotates
        # sub-TTL instead of waiting out the 300s Redis memo. ``None`` =
        # not wired → TTL backstop only (no behaviour change for existing
        # construction sites).
        self._corpus_version_service = corpus_version_service

    async def _invalidate_corpus_version(
        self,
        record_tenant_id: uuid.UUID | None,
        record_bot_id: uuid.UUID | None,
    ) -> None:
        """Bust the per-bot corpus_version Redis memo post-mutation.

        Skips when the service is unwired or either id is missing — the
        300s TTL backstop still converges those cases. The callee
        swallows Redis errors itself (best-effort by contract).
        """
        if (
            self._corpus_version_service is None
            or record_tenant_id is None
            or record_bot_id is None
        ):
            return
        await self._corpus_version_service.invalidate(
            record_tenant_id, record_bot_id,
        )

    async def _insert_stats_index(
        self,
        *,
        record_tenant_id: uuid.UUID,
        workspace_id: str,
        record_bot_id: uuid.UUID,
        record_document_id: uuid.UUID,
        entities: list,
    ) -> None:
        """Write parsed entities to ``document_service_index``.

        Best-effort: any DB error is logged and swallowed so a stats-index
        failure never blocks ingest.  The table is an enrichment layer, not
        part of the primary retrieval path.
        """
        if self._stats_index_repo is None or not entities:
            return
        try:
            await self._stats_index_repo.bulk_insert(
                record_tenant_id=record_tenant_id,
                workspace_id=workspace_id,
                record_bot_id=record_bot_id,
                record_document_id=record_document_id,
                entities=entities,
            )
        except Exception as exc:  # noqa: BLE001 — stats index is best-effort; ingest must not fail
            logger.warning(
                "stats_index_insert_failed",
                record_document_id=str(record_document_id),
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    async def _upsert_doc_summary(
        self,
        *,
        record_document_id: uuid.UUID,
        summary_json: dict,
    ) -> None:
        """UPDATE ``documents.summary_json`` with the stats aggregate.

        Best-effort: any DB error is logged and swallowed.
        """
        try:
            async with self._sf() as session:
                await session.execute(
                    text(
                        "UPDATE documents SET summary_json = CAST(:s AS jsonb) "
                        "WHERE id = :doc_id"
                    ),
                    {"s": json.dumps(summary_json), "doc_id": record_document_id},
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — summary update is best-effort
            logger.warning(
                "stats_index_summary_update_failed",
                record_document_id=str(record_document_id),
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    async def _embedding_spec(
        self,
        *,
        record_bot_id: uuid.UUID | None = None,
        record_tenant_id: uuid.UUID | None = None,
        language: str | None = None,
    ) -> EmbeddingSpec:
        """Resolve EmbeddingSpec — per-bot binding > system_config > settings.

        Resolver-first contract: when both ``record_bot_id`` and
        ``record_tenant_id`` are provided AND a model_resolver is wired, the
        per-bot ``bot_model_bindings`` row wins. Falls back to system_config
        on resolver failure (no binding, repo error) so bots without a
        binding row keep working.

        ``language`` (F12): the document's effective language code. When the
        operator configures ``system_config.embedding_model_by_language`` with
        an entry for this language, the resolved model NAME is swapped to the
        language-appropriate model (dimension/provider/task preserved). No map
        / no entry / ``language is None`` leaves the spec byte-identical.

        @return: EmbeddingSpec carrying the embedding model details
        """
        if (
            self._model_resolver is not None
            and record_bot_id is not None
            and record_tenant_id is not None
        ):
            try:
                spec = await self._model_resolver.resolve_embedding(
                    record_bot_id, record_tenant_id=record_tenant_id,
                )
                # Ingest path must always encode passages with the passage
                # head — asymmetric embedding models require it; the resolver
                # default may carry a query-task spec.
                if spec.task != DEFAULT_EMBEDDING_TASK_PASSAGE:
                    spec = spec.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_PASSAGE})
                return await self._apply_language_embedding_override(spec, language)
            except Exception as exc:  # noqa: BLE001 — resolver failure must fall back, not crash ingest
                logger.warning(
                    "embedding_resolver_fallback_to_system_config",
                    record_bot_id=str(record_bot_id),
                    record_tenant_id=str(record_tenant_id),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        # Fallback: system_config (Redis-cached) → settings.
        if self._cfg is not None:
            model_name = await self._cfg.get("embedding_model", self._settings.embedding.model_name)
            dimension = await self._cfg.get_int("embedding_dimension", self._settings.embedding.dimension)
            model_version = await self._cfg.get("embedding_model_version", self._settings.embedding.model_version)
        else:
            model_name = self._settings.embedding.model_name
            dimension = self._settings.embedding.dimension
            model_version = self._settings.embedding.model_version

        spec = EmbeddingSpec(
            binding_id=uuid.uuid4(),
            model_name=model_name,
            provider="litellm",
            dimension=dimension,
            max_batch=DEFAULT_EMBEDDING_MAX_BATCH,
            model_version=model_version,
            task=DEFAULT_EMBEDDING_TASK_PASSAGE,
        )
        return await self._apply_language_embedding_override(spec, language)

    async def _apply_language_embedding_override(
        self,
        spec: EmbeddingSpec,
        language: str | None,
    ) -> EmbeddingSpec:
        """Swap the embedding model NAME for the doc language (F12).

        Multi-language routing: a non-default-language document can resolve a
        language-appropriate embedding model when the operator configures
        ``system_config.embedding_model_by_language`` (JSONB {lang: model}).
        The map is empty by default, so this method is a pure pass-through
        (returns the identical spec object) on the single-model production
        path — byte-identical to pre-F12 behaviour.

        Only the model NAME is swapped; dimension/provider/task/model_version
        are preserved so the chunk vector column stays aligned with the query
        path's vector space. The operator owns mapping a dim-compatible model.
        """
        if not language or self._cfg is None:
            return spec
        lang_map = await self._cfg.get(
            "embedding_model_by_language",
            DEFAULT_EMBEDDING_MODEL_BY_LANGUAGE,
        )
        if not isinstance(lang_map, dict) or not lang_map:
            return spec
        mapped = lang_map.get(language)
        if not mapped or not isinstance(mapped, str) or mapped == spec.model_name:
            return spec
        logger.info(
            "embedding_model_language_override",
            language=language,
            from_model=spec.model_name,
            to_model=mapped,
            dimension=spec.dimension,
        )
        return spec.model_copy(update={"model_name": mapped})

    async def _embed_in_doc_batches(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        document_id: uuid.UUID,
        record_bot_id: uuid.UUID,
    ) -> list[Any]:
        """Embed ``texts`` in doc-level batches with progress + pacing.

        The embedder's own ``embed_batch`` already retries with backoff and
        chunks at the HTTP layer (``DEFAULT_EMBEDDING_MAX_BATCH``). The doc-
        level loop is one layer above: it slices the orchestrator-side list
        so a 3851-chunk document doesn't sit on one giant await with zero
        observability, and so we yield to the event loop + pace the
        provider QPS between rounds.

        Caller is responsible for the length-mismatch guard against
        ``_chunks_needing_embed`` — this helper only assembles the embed
        vectors returned by the embedder; ``len(out) == len(texts)`` is
        the only contract it owns.
        """
        doc_batch_size = DEFAULT_EMBED_DOC_BATCH_SIZE
        inter_batch_sleep_s = DEFAULT_EMBED_INTER_BATCH_SLEEP_S
        if self._cfg is not None:
            doc_batch_size = await self._cfg.get_int(
                "embed_doc_batch_size", DEFAULT_EMBED_DOC_BATCH_SIZE,
            )
            inter_batch_sleep_s = await self._cfg.get_float(
                "embed_inter_batch_sleep_s",
                DEFAULT_EMBED_INTER_BATCH_SLEEP_S,
            )
        # ``get_int`` may return 0 if an operator misconfigures the row;
        # clamp to the constant default to avoid a ``range(0, n, 0)``
        # ValueError downstream. Negative sleep collapses to default too.
        if doc_batch_size <= 0:
            doc_batch_size = DEFAULT_EMBED_DOC_BATCH_SIZE
        if inter_batch_sleep_s < 0:
            inter_batch_sleep_s = DEFAULT_EMBED_INTER_BATCH_SLEEP_S

        total_texts = len(texts)
        total_batches = (
            (total_texts + doc_batch_size - 1) // doc_batch_size
            if total_texts > 0
            else 0
        )
        accumulated: list[Any] = []
        for batch_idx, batch_start in enumerate(
            range(0, total_texts, doc_batch_size),
        ):
            raw_slice = texts[batch_start : batch_start + doc_batch_size]
            # Dual-field: embed the canonical form (URLs + redundant whitespace
            # stripped) — raw chunk text is persisted to ``content`` separately
            # for BM25. Cuts token waste + vector dilution; default ON.
            batch_slice = [canonicalize_embed_text(t) for t in raw_slice]
            try:
                batch_out = await asyncio.wait_for(
                    self._embedder.embed_batch(
                        batch_slice, spec=spec, record_tenant_id=None,
                    ),
                    timeout=DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S,
                )
            except TimeoutError as exc:
                # Hard ceiling hit — a hung provider await must NOT stall the
                # worker on this document forever. Convert to the fail-loud
                # path the caller already handles (mark doc failed + recovery
                # re-queues) rather than silently emitting document_ingested
                # with 0 persisted chunks.
                logger.error(
                    "embed_batch_timeout_aborting_ingest",
                    document_id=str(document_id),
                    record_bot_id=str(record_bot_id),
                    batch_idx=batch_idx,
                    batch_chunks=len(batch_slice),
                    timeout_s=DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S,
                )
                raise ExternalServiceError(
                    f"embed batch {batch_idx} exceeded "
                    f"{DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S}s for document "
                    f"{document_id}",
                ) from exc
            accumulated.extend(batch_out)
            logger.info(
                "embed_batch_progress",
                document_id=str(document_id),
                record_bot_id=str(record_bot_id),
                batch_idx=batch_idx,
                total_batches=total_batches,
                chunks_done=len(accumulated),
                chunks_total=total_texts,
            )
            # Skip the inter-batch sleep on the final batch: we want
            # backpressure between provider calls, not extra latency at
            # the tail of the document. TODO(admin): when ``documents``
            # gains ``chunks_embedded`` / ``embedded_at`` columns, write
            # progress to the DB here so external observers can poll.
            if batch_start + doc_batch_size < total_texts:
                await asyncio.sleep(inter_batch_sleep_s)
        return accumulated

    async def _resolve_embedding_passage_prefix(
        self,
        *,
        record_bot_id: uuid.UUID,
        record_tenant_id: uuid.UUID | None = None,
    ) -> str:
        """Resolve the asymmetric-embedding passage prefix per-bot.

        Resolution chain (first non-empty wins):
        1. ``bots.plan_limits.embedding_passage_prefix`` (per-bot, multi-vertical
           override — e.g. healthcare = ``"medical_record: "``).
        2. ``system_config.embedding_passage_prefix`` (platform default).
        3. ``DEFAULT_EMBEDDING_PASSAGE_PREFIX`` (empty by default — opt-in).

        Re-embedding is required for changes to take effect.
        """
        # 1. Per-bot column from ``bots.plan_limits`` (JSONB).
        try:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                row = await session.execute(
                    text("SELECT plan_limits FROM bots WHERE id = :bid"),
                    {"bid": record_bot_id},
                )
                fetched = row.first()
        except (OSError, ConnectionError, TimeoutError) as exc:
            logger.warning(
                "embedding_passage_prefix_lookup_failed",
                record_bot_id=str(record_bot_id),
                error=str(exc),
            )
            fetched = None
        if fetched is not None:
            plan_limits = fetched[0] or {}
            if isinstance(plan_limits, dict):
                bot_prefix = plan_limits.get("embedding_passage_prefix")
                if isinstance(bot_prefix, str) and bot_prefix:
                    return bot_prefix.strip('"')

        # 2. System-wide default from system_config (Redis-cached).
        if self._cfg is not None:
            raw = await self._cfg.get(
                "embedding_passage_prefix",
                DEFAULT_EMBEDDING_PASSAGE_PREFIX,
            )
            if isinstance(raw, str) and raw:
                return raw.strip('"')

        # 3. Constant fallback.
        return DEFAULT_EMBEDDING_PASSAGE_PREFIX

    async def _resolve_chunking_policy(
        self,
        record_bot_id: uuid.UUID,
        *,
        record_tenant_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Resolve the effective chunking policy for a bot.

        per-bot ``plan_limits.chunking_config`` > platform
        ``system_config.chunking_policy`` > constants. Behaviour-neutral by
        default (``table_strategy = DEFAULT_TABLE_STRATEGY``, no force).
        Re-ingest is required for changes to take effect.
        """
        plan_limits: Any = None
        try:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                row = await session.execute(
                    text("SELECT plan_limits FROM bots WHERE id = :bid"),
                    {"bid": record_bot_id},
                )
                fetched = row.first()
            if fetched is not None:
                plan_limits = fetched[0]
        except (OSError, ConnectionError, TimeoutError) as exc:
            logger.warning(
                "chunking_policy_lookup_failed",
                record_bot_id=str(record_bot_id),
                error=str(exc),
            )

        platform_policy: Any = None
        if self._cfg is not None:
            platform_policy = await self._cfg.get("chunking_policy", None)

        return resolve_chunking_policy(
            plan_limits=plan_limits, platform_policy=platform_policy,
        )

    async def _resolve_embedding_text_strategy_name(
        self,
        *,
        record_bot_id: uuid.UUID,
        record_tenant_id: uuid.UUID | None = None,
    ) -> str:
        """Resolve the embedding-text strategy provider key per-bot.

        Resolution chain (first non-empty wins, mirrors the passage-prefix
        resolver above):

        1. ``bots.plan_limits.embedding_text_strategy`` (per-bot override —
           e.g. legal/regulatory bots set ``"raw_only"`` to fix short-keyword
           dilution; other bots stay on ``"prefix_plus_raw"``).
        2. ``system_config.embedding_text_strategy`` (platform default).
        3. ``DEFAULT_EMBEDDING_TEXT_STRATEGY`` constant.

        Re-embedding REQUIRED for changes to take effect — existing rows
        keep whatever strategy they were ingested under.
        """
        # 1. Per-bot column from ``bots.plan_limits`` (JSONB).
        try:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                row = await session.execute(
                    text("SELECT plan_limits FROM bots WHERE id = :bid"),
                    {"bid": record_bot_id},
                )
                fetched = row.first()
        except (OSError, ConnectionError, TimeoutError) as exc:
            logger.warning(
                "embedding_text_strategy_lookup_failed",
                record_bot_id=str(record_bot_id),
                error=str(exc),
            )
            fetched = None
        if fetched is not None:
            plan_limits = fetched[0] or {}
            if isinstance(plan_limits, dict):
                bot_choice = plan_limits.get("embedding_text_strategy")
                if isinstance(bot_choice, str) and bot_choice:
                    return bot_choice.strip().lower()

        # 2. System-wide default from system_config (Redis-cached).
        if self._cfg is not None:
            raw = await self._cfg.get(
                "embedding_text_strategy",
                DEFAULT_EMBEDDING_TEXT_STRATEGY,
            )
            if isinstance(raw, str) and raw:
                return raw.strip().lower()

        # 3. Constant fallback.
        return DEFAULT_EMBEDDING_TEXT_STRATEGY

    async def _resolve_chunk_hash_id_enabled(
        self,
        *,
        record_bot_id: uuid.UUID,
        record_tenant_id: uuid.UUID | None = None,
    ) -> bool:
        """Resolve the per-bot ``chunk_hash_id_enabled`` opt-in flag.

        Resolution chain (first non-empty wins, mirrors the resolvers
        above):

        1. ``bots.plan_limits.chunk_hash_id_enabled`` (per-bot column).
        2. ``system_config.chunk_hash_id_enabled`` (platform default).
        3. ``DEFAULT_CHUNK_HASH_ID_ENABLED`` constant (False — the
           ``uuid.uuid4()`` path is preserved).

        When True the ingest path stamps each chunk with a deterministic
        UUID5 derived from ``(record_bot_id, document_id, content)``;
        re-ingest of the same content yields the same UUIDs (idempotent
        UPSERT). Defaults to False so existing bots keep their current
        IDs until opting in explicitly.
        """
        try:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                row = await session.execute(
                    text("SELECT plan_limits FROM bots WHERE id = :bid"),
                    {"bid": record_bot_id},
                )
                fetched = row.first()
        except (OSError, ConnectionError, TimeoutError) as exc:
            logger.warning(
                "chunk_hash_id_lookup_failed",
                record_bot_id=str(record_bot_id),
                error=str(exc),
            )
            fetched = None
        if fetched is not None:
            plan_limits = fetched[0] or {}
            if isinstance(plan_limits, dict):
                bot_choice = plan_limits.get("chunk_hash_id_enabled")
                if isinstance(bot_choice, bool):
                    return bot_choice

        if self._cfg is not None:
            try:
                raw = await self._cfg.get(
                    "chunk_hash_id_enabled",
                    DEFAULT_CHUNK_HASH_ID_ENABLED,
                )
                if isinstance(raw, bool):
                    return raw
                if isinstance(raw, str):
                    return raw.strip().lower() in {"1", "true", "yes", "on"}
            except (OSError, ConnectionError, TimeoutError):
                pass

        return DEFAULT_CHUNK_HASH_ID_ENABLED

    @staticmethod
    def _compute_chunk_hashes(texts: list[str]) -> list[str]:
        """SHA-256 fingerprint of the text fed to the embedder.

        Caller MUST pass the post-enrichment list — feeding raw chunks here
        keeps the cached hash stable across enrichment-context changes,
        which makes the incremental re-index path skip the embedder and
        leave a stale vector under a misleading ``content_hash``.
        """
        return [
            hashlib.sha256(t.encode()).hexdigest()[:DEFAULT_CONTENT_HASH_HEX_LEN]
            for t in texts
        ]

    @staticmethod
    def _file_ext_from(file_name: str | None) -> str:
        """Return lowercase extension (with dot) for ``file_name`` or empty."""
        if not file_name:
            return ""
        idx = file_name.rfind(".")
        return file_name[idx:].lower() if idx >= 0 else ""

    async def _route_through_parser(
        self,
        raw_bytes: bytes,
        *,
        mime_type: str,
        file_name: str,
    ) -> tuple[str | None, list[dict] | None]:
        """Run the registry parser for ``mime_type`` over ``raw_bytes``.

        Returns ``(joined_text, parser_chunks)``:
          - ``joined_text`` — concatenated content (preserves section markers
            for the text-based chunking path).
          - ``parser_chunks`` — original list of dicts emitted by the parser,
            so callers that prefer row-as-chunk semantics (Excel, Sheets CSV)
            can bypass ``smart_chunk`` and avoid the flatten + re-chunk that
            would destroy row boundaries.

        Returns ``(None, None)`` when no provider supports the mime/ext.
        Returns ``("", [])`` when a provider matched but yielded no chunks.
        """
        ext = self._file_ext_from(file_name)
        parser = self._parser_detector(mime_type, ext)
        if parser is None and raw_bytes:
            # One-flow rule: a URL pdf/docx fetched with an empty/generic
            # mime and no extension must still route to the structured
            # parser via byte-sniff, not silently fall back to flat OCR.
            parser = detect_parser_robust(
                mime_type, ext, raw_bytes, detector=self._parser_detector
            )
        if parser is None:
            logger.warning(
                "ingest_no_parser_match",
                mime_type=mime_type,
                file_ext=ext,
                file_name=file_name,
                bytes_len=len(raw_bytes) if raw_bytes else 0,
            )
            return None, None

        chunks = await parser.parse(raw_bytes, file_name=file_name)
        if not chunks:
            return "", []

        provider_name = (
            parser.get_provider_name() if hasattr(parser, "get_provider_name") else "unknown"
        )
        logger.info(
            "ingest_parser_registry_routed",
            provider=provider_name,
            mime_type=mime_type,
            file_name=file_name,
            chunks=len(chunks),
            bytes=len(raw_bytes),
        )
        joined = "\n\n".join(c["content"] for c in chunks if c.get("content"))

        # Format→markdown normalizer (config-gated, default OFF). When
        # enabled, raw CSV regions become markdown pipe tables + VN legal
        # markers become ATX headings BEFORE chunking. Only the joined text is
        # normalised; parser row-chunks (excel/sheets) keep their own shape.
        if self._cfg is not None and joined:
            normalize_on = bool(
                await self._cfg.get(
                    "markdown_normalize_enabled",
                    DEFAULT_MARKDOWN_NORMALIZE_ENABLED,
                )
            )
            if normalize_on:
                joined = normalize_to_markdown(joined)
        return joined, chunks

    async def _extract_metadata_llm(self, content: str, title: str) -> dict:
        """LLM extract metadata. Returns {} when no vocabulary configured."""
        try:
            import litellm as _litellm

            if self._cfg is not None:
                model = await self._cfg.get("metadata_extraction_model", DEFAULT_METADATA_EXTRACTION_MODEL)
                vocabulary_raw = await self._cfg.get("metadata_extraction_vocabulary", "")
                system_prompt = await self._cfg.get("metadata_extraction_system_prompt", "")
            else:
                model = DEFAULT_METADATA_EXTRACTION_MODEL
                vocabulary_raw = ""
                system_prompt = ""

            if not system_prompt:
                return {}

            preview = content[:DEFAULT_CONTENT_PREVIEW_CHARS]
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Document title: {title}\n\n{preview}"},
            ]
            resp = await _litellm.acompletion(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=DEFAULT_METADATA_MAX_TOKENS,
                timeout=DEFAULT_HTTP_TIMEOUT_S,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            return json.loads(raw)
        except Exception:  # noqa: BLE001 — provider error or malformed JSON: skip metadata
            logger.warning("metadata_extraction_failed", title=title, exc_info=True)
            return {}

    async def replace_documents_for_bot(
        self,
        record_bot_id: uuid.UUID,
        *,
        source_urls: list[str],
        record_tenant_id: uuid.UUID | None = None,
    ) -> tuple[int, int]:
        """UPSERT-safe replace — soft-delete only docs matching incoming URLs.

        Deleting every document for the bot before re-ingesting would let a
        partial sync (1 doc) wipe the entire knowledge base, so this method
        replaces only documents whose ``source_url`` is in the incoming
        payload. Existing docs not referenced in this batch are preserved.

        Implementation: HARD-delete the matched docs' ``document_chunks``
        (no FK cascade exists), then SOFT-delete the document rows
        (``deleted_at = now()``) for forensic / rollback. The subsequent
        :meth:`ingest` re-uses the same document_id (``uq_doc_tool`` ON
        CONFLICT) and re-inserts fresh chunks — purging here is what keeps
        the re-ingest from leaving stale chunks behind (duplication).

        @param record_bot_id: UUID of the owning bot
        @param source_urls: list of source URLs from the incoming sync
            payload — only docs matching one of these URLs will be
            soft-deleted. Empty/None URLs are skipped (cannot dedup).
        @param record_tenant_id: tenant binding for RLS
        @return: (chunks_purged, documents_soft_deleted) — chunks_purged
            is the count of stale chunks hard-deleted for the replaced docs.
        """
        # Filter empty source URLs — cannot dedup without an identity
        clean_urls = [u for u in source_urls if u]
        if not clean_urls:
            logger.info(
                "replace_documents_for_bot_noop",
                record_bot_id=str(record_bot_id),
                reason="no_source_urls_provided",
            )
            return (0, 0)

        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            # PURGE the chunks of the docs being replaced FIRST. The subsequent
            # re-ingest re-uses the SAME document_id (``uq_doc_tool`` ON CONFLICT
            # resurrects the soft-deleted row) and the ingest dedup SELECT's
            # ``deleted_at IS NULL`` filter makes ``is_reindex=False`` → the store
            # stage does a pure INSERT of the new chunks. There is NO FK cascade
            # on ``document_chunks`` — without this explicit purge the OLD chunks
            # survive alongside the NEW ones (duplication).
            # Purge ALL content-state tables (chunks + service_index) via the single
            # source of truth — the re-ingest path used to purge chunks only, leaving
            # stale ``document_service_index`` col_N rows.
            chunks_purged = await _purge_content_tables(
                session,
                doc_filter="record_bot_id = :bid AND source_url = ANY(:urls) AND deleted_at IS NULL",
                params={"bid": record_bot_id, "urls": clean_urls},
            )
            # Soft-delete docs with overlapping source_url (UPSERT replace)
            r = await session.execute(
                text("""UPDATE documents
                        SET deleted_at = now()
                        WHERE record_bot_id = :bid
                          AND source_url = ANY(:urls)
                          AND deleted_at IS NULL"""),
                {"bid": record_bot_id, "urls": clean_urls},
            )
            # Invalidate semantic_cache for this bot so stale answers don't survive.
            rc = await session.execute(
                text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
                {"bid": record_bot_id},
            )
            await session.commit()
            await self._invalidate_corpus_version(
                record_tenant_id, record_bot_id,
            )
            logger.info(
                "replace_documents_for_bot",
                record_bot_id=str(record_bot_id),
                docs_replaced=r.rowcount or 0,
                chunks_purged=chunks_purged,
                urls_count=len(clean_urls),
                semantic_cache_purged=rc.rowcount or 0,
            )
            return (chunks_purged, r.rowcount or 0)

    async def delete_all_for_bot(
        self,
        record_bot_id: uuid.UUID,
        *,
        record_tenant_id: uuid.UUID | None = None,
    ) -> tuple[int, int]:
        """Delete every document and chunk belonging to a bot.

        Also invalidates semantic_cache so stale answers don't survive a
        corpus mutation. ``record_bot_id`` alone is enough (1:1 with the
        external 3-key triple). ``record_tenant_id`` may be omitted only
        when an upstream context binding exists.
        """
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            # Purge ALL content-state tables (chunks + service_index) — this bot-wide
            # path also used to forget document_service_index.
            chunks_deleted = await _purge_content_tables(
                session, doc_filter="record_bot_id = :bid", params={"bid": record_bot_id},
            )
            r2 = await session.execute(
                text("DELETE FROM documents WHERE record_bot_id = :bid"),
                {"bid": record_bot_id},
            )
            # Purge semantic_cache for this bot so stale answers don't linger.
            rc = await session.execute(
                text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
                {"bid": record_bot_id},
            )
            await session.commit()
            await self._invalidate_corpus_version(
                record_tenant_id, record_bot_id,
            )
            logger.info(
                "semantic_cache_invalidated",
                reason="delete_all_for_bot",
                record_bot_id=str(record_bot_id),
                rows_deleted=rc.rowcount or 0,
            )
            return (chunks_deleted, r2.rowcount or 0)

    async def delete_document(
        self,
        doc_uuid: uuid.UUID,
        *,
        record_tenant_id: uuid.UUID | None = None,
    ) -> bool:
        """Delete a single document and all of its chunks.

        Also purges the owning bot's semantic_cache so the system never
        returns an answer grounded in a chunk that has been deleted.

        @param doc_uuid: UUID of the document to delete
        @param record_tenant_id: tenant UUID — explicit binding for RLS.
            Optional only because route-layer callers bind via
            ``tenant_id_ctx`` upstream.
        @return: True when the deletion succeeds
        """
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            # Resolve owning bot + tenant FIRST (before soft-delete masks
            # the row) — the tenant feeds the corpus_version bust key.
            bot_row = await session.execute(
                text(
                    "SELECT record_bot_id, record_tenant_id "
                    "FROM documents WHERE id = :id",
                ),
                {"id": doc_uuid},
            )
            bot_match = bot_row.fetchone()
            record_bot_id = bot_match[0] if bot_match else None
            owning_tenant_id = bot_match[1] if bot_match else None

            # Purge ALL content-state tables (chunks + service_index) via the single
            # source of truth (ING-7: reclaim pre-extracted stats-index entities too,
            # so price/list/keyword routes don't accumulate dead entities).
            await _purge_content_tables(
                session, doc_filter="id = :id", params={"id": doc_uuid},
            )
            await session.execute(
                text("UPDATE documents SET deleted_at = now() WHERE id = :id"), {"id": doc_uuid},
            )
            # Invalidate semantic_cache for the bot that owned this doc.
            rows_cache = 0
            if record_bot_id is not None:
                rc = await session.execute(
                    text("DELETE FROM semantic_cache WHERE record_bot_id = :bid"),
                    {"bid": record_bot_id},
                )
                rows_cache = rc.rowcount or 0
            await session.commit()
            await self._invalidate_corpus_version(
                owning_tenant_id, record_bot_id,
            )
            logger.info(
                "semantic_cache_invalidated",
                reason="delete_document",
                document_id=str(doc_uuid),
                record_bot_id=str(record_bot_id) if record_bot_id else None,
                rows_deleted=rows_cache,
            )
            return True


__all__ = ["DocumentService", "IngestResult"]
