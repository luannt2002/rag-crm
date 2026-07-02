"""DocumentService ingest pipeline (U1-U7) as a mixin.

The 2.7k-line ``ingest()`` method is the core ingest path; it lives here as a
mixin so ``DocumentService`` (in __init__) stays a navigable skeleton. ``self``
resolves all collaborators (the resolver/embedding-spec/metadata methods remain
on DocumentService). Behaviour is identical — this is a pure relocation.
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

from ragbot.application.services.document_service.ingest_phases import *  # noqa: E402,F401,F403
from ragbot.application.services.document_service.ingest_helpers import *  # noqa: E402,F401,F403
from ragbot.application.services.document_service.text_processing import *  # noqa: E402,F401,F403

# Stage methods (U3-U7 + finalize) live in sibling ``ingest_stages*`` mixins so
# this file stays a navigable skeleton (and each stage file stays ≤1200 lines).
# The imports are placed after the module-level names above are bound because
# those modules reference this module (``ingest_core``) for the patchable
# ``session_with_tenant`` / ``_bulk_insert_chunks`` seams at call time (not
# import time).
from ragbot.application.services.document_service.ingest_stages import (  # noqa: E402
    _IngestCtx,
    _StageChunkMixin,
)
from ragbot.application.services.document_service.ingest_stages_enrich import (  # noqa: E402
    _StageEnrichMixin,
)
from ragbot.application.services.document_service.ingest_stages_store import (  # noqa: E402
    _StageStoreMixin,
)
from ragbot.application.services.document_service.ingest_stages_final import (  # noqa: E402
    _StageFinalizeMixin,
)


class _IngestMixin(
    _StageChunkMixin,
    _StageEnrichMixin,
    _StageStoreMixin,
    _StageFinalizeMixin,
):
    """Provides ``DocumentService.ingest`` (U1-U7). Mix into DocumentService."""

    async def ingest(
        self,
        *,
        record_bot_id: uuid.UUID,
        title: str,
        content: str,
        source_url: str = "",
        source_type: str = "manual",
        language: str = "auto",
        mime_type: str = "text/plain",
        existing_doc_id: uuid.UUID | None = None,
        record_tenant_id: uuid.UUID | None = None,
        workspace_id: str | None = None,
        channel_type: str = "web",
        raw_bytes: bytes | None = None,
        file_name: str | None = None,
        blocks: list[Any] | None = None,
        step_tracker: Any | None = None,
    ) -> IngestResult:
        """Chia nội dung thành chunk, tạo embedding và lưu vào document_chunks.

        @param blocks: structure-aware ``list[Block]`` from the parser
            (ADR-W3-D1 S1). When present, the structural type/atomicity the
            parser already computed is carried end-to-end instead of being
            flattened to ``content`` and re-detected from markdown downstream
            (the P2-B mis-narration root). ``None`` (direct-text API / legacy
            callers) → the str-based ``smart_chunk(content)`` path is unchanged.
            S1 only THREADS the blocks; the block-native chunking flip (S2/S3)
            lands behind a graded A/B gate.

        Hỗ trợ incremental re-indexing: nếu existing_doc_id được cung cấp,
        so sánh content_hash của từng chunk để chỉ embed lại chunk thay đổi.

        @param record_bot_id: UUID của bot sở hữu tài liệu
        @param title: tiêu đề tài liệu
        @param content: nội dung văn bản (đã extract); dùng làm fallback khi
            không có ``raw_bytes`` hoặc parser registry không match.
        @param source_url: URL nguồn của tài liệu
        @param source_type: loại nguồn (manual, google_docs, ...)
        @param existing_doc_id: UUID tài liệu đã tồn tại (để re-index thay vì tạo mới)
        @param workspace_id: tenant-scoped slug bot owns; mirrored from the
            bot row so the document INSERT carries the same scope. Caller
            (worker / route) resolves ``None`` to ``str(record_tenant_id)``
            before calling so the INSERT never fails the NOT NULL CHECK.
        @param channel_type: loại kênh (web, zalo, ...)
        @param raw_bytes: raw upload bytes; khi cung cấp + ``mime_type``/ext
            khớp 1 parser trong registry, parser sẽ extract text và override
            ``content``. Pass-through khi ``None``.
        @param file_name: tên file gốc (dùng để lookup ext + put vào chunk
            metadata). Optional khi ``raw_bytes`` None.
        @return: IngestResult chứa thông tin kết quả nhập
        """
        # Structure-aware Block stream observability (ADR-W3-D1 S1). The
        # parser already computed each block's type/atomicity; logging the
        # histogram here proves the stream survives to ingest (instead of
        # being flattened at document_worker.py:298) WITHOUT yet changing how
        # chunks are produced — the block-native flip is S2/S3, A/B-gated.
        if blocks:
            _block_type_counts: dict[str, int] = {}
            for _b in blocks:
                _bt = str(getattr(_b, "type", "TEXT"))
                _block_type_counts[_bt] = _block_type_counts.get(_bt, 0) + 1
            logger.info(
                "ingest_block_stream_received",
                record_bot_id=str(record_bot_id),
                block_count=len(blocks),
                block_types=_block_type_counts,
            )

        # Defensive resolution: when the caller did not thread a slug, fall
        # back via the central resolver (single source of truth — same rule
        # as the route layer). ``record_tenant_id`` may still be ``None`` on
        # legacy callers; in that case we cannot fabricate a slug and the
        # downstream INSERT will surface the missing-tenant error instead.
        if workspace_id is None and record_tenant_id is not None:
            from ragbot.shared.workspace_id_validator import resolve_workspace_id
            workspace_id = resolve_workspace_id(
                None, record_tenant_id=record_tenant_id,
            )

        # 2026-05-27 — sniff real MIME when declared is ambiguous
        # (octet-stream / empty). Closes silent-fail bug where parser
        # registry returned None for octet-stream uploads → 0 chunks
        # ingested. The sniff is a pure function over raw_bytes; only
        # applies when raw_bytes is present.
        from ragbot.shared.mime_sniff import sniff_real_mime
        _pre_sniff_mime = mime_type
        if raw_bytes is not None:
            mime_type = sniff_real_mime(raw_bytes, file_name or "", mime_type or "")
            if mime_type != _pre_sniff_mime:
                logger.info(
                    "ingest_mime_sniff_corrected",
                    declared=_pre_sniff_mime,
                    sniffed=mime_type,
                    file_name=file_name,
                    bytes_len=len(raw_bytes),
                )

        # ── U1 ingest_validate — tenant guard + initial sanity ──
        # Phase D observability: pure metadata wrap; no logic change.
        async with _phase_d_step(step_tracker, "ingest_validate") as _u1_ctx:
            if record_tenant_id is None:
                logger.warning("ingest_missing_tenant_id", bot_id=str(record_bot_id))
            _u1_ctx.set_metadata(
                n_bytes=(len(raw_bytes) if raw_bytes is not None else len(content)),
                mime_detected=mime_type,
                language_in=language,
                channel_type=channel_type,
                is_reindex=existing_doc_id is not None,
            )

        # ── Source URL allow-list gate (T1-Safety) ──
        # PoisonedRAG arXiv 2402.07867 defence: reject adversary-
        # controlled URLs BEFORE chunk/embed work runs. Two-knob opt-in
        # (system_config.source_allowlist_enabled + per-bot
        # plan_limits.allowed_source_domains non-empty) keeps existing
        # tenants on the legacy passthrough until they configure both.
        # Raises SourceNotAllowedError on hard reject — surfaces as
        # HTTP 422 SOURCE_NOT_ALLOWED at the route layer.
        await _maybe_validate_source_allowlist(
            source_url,
            source_validator=self._source_validator,
            bot_repo=self._bot_repo,
            config_service=self._cfg,
            record_bot_id=record_bot_id,
            record_tenant_id=record_tenant_id,
        )

        # ── U2 ingest_parse — DocumentParserPort registry hot-path routing ──
        # When the caller provides raw bytes + mime, ask the registry whether
        # any registered parser supports the (mime, ext) pair. Hit → parser
        # extracts structured text (section markers, table-aware) which then
        # replaces ``content`` for the rest of the pipeline. Miss → fall
        # through to the legacy string pass-through path.
        # G2 fix (Stream A Phase 2): preserve parser row-chunks so the
        # downstream chunker can bypass smart_chunk for table-shaped input
        # (Excel, Google Sheets CSV) and keep 1-row → 1-chunk semantics.
        parser_row_chunks: list[dict] | None = None
        async with _phase_d_step(step_tracker, "ingest_parse") as _u2_ctx:
            _u2_parser_provider = "passthrough"
            _u2_chars_in = len(content)
            if raw_bytes is not None:
                extracted: str | None = None
                try:
                    extracted, parser_row_chunks = await self._route_through_parser(
                        raw_bytes,
                        mime_type=mime_type,
                        file_name=file_name or title,
                    )
                except (ValueError, NotImplementedError) as exc:
                    logger.warning(
                        "ingest_parser_registry_failed",
                        mime_type=mime_type,
                        file_name=file_name,
                        error=str(exc),
                    )
                    extracted = None
                    parser_row_chunks = None
                if extracted is not None:
                    content = extracted
                    _u2_parser_provider = "registry"
            _u2_ctx.set_metadata(
                parser_provider=_u2_parser_provider,
                mime_type=mime_type,
                n_chars_in=_u2_chars_in,
                n_chars_out=len(content),
                n_pages=None,  # parsers don't currently surface page count
            )

        # ── PII redaction at the INGEST BOUNDARY (Master Finding #4) ──
        content = await _maybe_redact_ingest_content(
            content,
            pii_redactor=self._pii_redactor,
            bot_repo=self._bot_repo,
            record_bot_id=record_bot_id,
            record_tenant_id=record_tenant_id,
        )

        # Pipeline audit logger leader-trace.
        _audit = self._audit
        _audit_bot = str(record_bot_id)
        _ingest_t0 = time.perf_counter()
        if _audit is not None:
            await _audit.log(
                _audit_bot,
                "ingest",
                "ingest_started",
                {
                    "title": title,
                    "source_url": source_url,
                    "source_type": source_type,
                    "mime_type": mime_type,
                    "language": language,
                    "raw_len": len(content),
                    "channel_type": channel_type,
                    "is_reindex": existing_doc_id is not None,
                },
            )

        # ── Memory safety: reject oversized content before any processing ──
        if self._cfg is not None:
            max_content_chars = await self._cfg.get_int("max_ingest_content_chars", MAX_DOCUMENT_CONTENT_CHARS)
        else:
            max_content_chars = MAX_DOCUMENT_CONTENT_CHARS
        if len(content) > max_content_chars:
            raise ValueError(
                f"Content too large: {len(content)} chars (max {max_content_chars})"
            )

        # Re-upload dedup. If caller did NOT pass existing_doc_id but a
        # live document with the same (record_bot_id, source_url) already
        # exists, route through the incremental re-index path so we do
        # not create a duplicate row regardless of idempotency-cache TTL.
        # Filter on record_bot_id only — uq_bots_tenant_bot_channel makes
        # it 1:1 with (tenant_id, bot_id, channel_type).
        if existing_doc_id is None and source_url:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                dup_row = await session.execute(
                    text(
                        """
                        SELECT id FROM documents
                        WHERE record_bot_id = :bid
                          AND source_url = :url
                          AND deleted_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {"bid": record_bot_id, "url": source_url},
                )
                match = dup_row.fetchone()
                if match is not None:
                    existing_doc_id = match[0]
                    logger.info(
                        "ingest_reusing_existing_document",
                        record_bot_id=str(record_bot_id),
                        channel_type=channel_type,
                        source_url=source_url,
                        document_id=str(existing_doc_id),
                    )

        is_reindex = existing_doc_id is not None
        doc_id = existing_doc_id if is_reindex else uuid.uuid4()
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Content-hash dedup at INGEST.
        # Block insertion of a brand-new document whose raw_content sha256
        # already exists (live, same bot). Re-index path (existing_doc_id
        # supplied or source_url match) is exempt because that's an
        # idempotent update, not a duplicate. Tests:
        # `tests/unit/test_content_hash_dedup.py`.
        if not is_reindex:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                dup_hash_row = await session.execute(
                    text(
                        """
                        SELECT id FROM documents
                        WHERE record_bot_id = :bid
                          AND content_hash = :h
                          AND deleted_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {"bid": record_bot_id, "h": content_hash},
                )
                if dup_hash_row.fetchone() is not None:
                    logger.warning(
                        "ingest_duplicate_content_hash",
                        record_bot_id=str(record_bot_id),
                        content_hash=content_hash[:16],
                        title=title,
                    )
                    raise DocumentDuplicateError(
                        f"Document with identical content already exists for bot {record_bot_id}"
                    )

        # Insert or update document record
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            if is_reindex:
                await session.execute(
                    text("""
                        UPDATE documents
                        SET content_hash = :content_hash,
                            version = version + 1,
                            document_name = :document_name,
                            content_chars = :content_chars,
                            raw_content = :raw_content,
                            metadata_json = CAST(:metadata AS jsonb)
                        WHERE id = :id
                    """),
                    {
                        "id": doc_id,
                        "content_hash": content_hash,
                        "document_name": title,
                        "content_chars": len(content),
                        "raw_content": content,
                        "metadata": json.dumps({
                            "source_type": source_type,
                            "original_title": title,
                        }),
                    },
                )
            else:
                # AdapChunk plan 260528 Phase 2 — race-safe document upsert.
                # ON CONFLICT prevents UniqueViolation when 2 parallel requests
                # try to INSERT the same (record_tenant_id, record_bot_id,
                # tool_name) tuple. Previous plain INSERT raised
                # asyncpg.UniqueViolationError → middleware swallowed → HTTP
                # 500 generic. PG UPSERT is atomic at DB level (no app-side
                # coordination needed), idempotent, and preserves
                # `content_hash` based skip-embed semantics downstream.
                # `xmax=0` in RETURNING signals fresh INSERT vs UPDATE.
                # RETURNING id is load-bearing: on ON CONFLICT DO UPDATE the
                # EXISTING row keeps its OWN id (not the freshly-generated
                # ``doc_id``). Without reading it back, the downstream chunk
                # INSERTs reference a id that was never persisted →
                # fk_chunks_document "doc not present" 500 on re-ingest of any
                # doc sharing a (tenant, bot, tool_name) tuple. Reconcile below.
                _doc_upsert = await session.execute(
                    text("""
                        INSERT INTO documents (id, record_bot_id, record_tenant_id, workspace_id, source_url, document_name, tool_name,
                            mime_type, language, state, version, content_hash, acl, metadata_json, content_chars, raw_content)
                        VALUES (:id, :record_bot_id, :record_tenant_id, :workspace_id, :source_url, :document_name, :tool_name,
                            :mime_type, :language, 'active', 1, :content_hash, :acl, CAST(:metadata AS jsonb), :content_chars, :raw_content)
                        ON CONFLICT ON CONSTRAINT uq_doc_tool DO UPDATE SET
                            source_url = EXCLUDED.source_url,
                            document_name = EXCLUDED.document_name,
                            mime_type = EXCLUDED.mime_type,
                            content_hash = EXCLUDED.content_hash,
                            metadata_json = EXCLUDED.metadata_json,
                            content_chars = EXCLUDED.content_chars,
                            raw_content = EXCLUDED.raw_content,
                            version = documents.version + 1,
                            updated_at = now()
                        RETURNING id
                    """),
                    {
                        "id": doc_id,
                        "record_bot_id": record_bot_id,
                        "record_tenant_id": record_tenant_id,
                        "workspace_id": workspace_id,
                        "source_url": source_url,
                        "document_name": title,
                        "tool_name": title.lower().replace(" ", "_")[:64],
                        "mime_type": mime_type,
                        # Domain-neutral: when caller passes "auto", fall back
                        # to the deployment-wide ``DEFAULT_LANGUAGE``. Multi-
                        # industry deployments override per-bot via ``bots.language``
                        # column, which the caller (HTTP /sync) propagates as the
                        # ``language`` argument so non-VN tenants are honored.
                        "language": language if language != "auto" else DEFAULT_LANGUAGE,
                        "content_hash": content_hash,
                        "acl": [],
                        "content_chars": len(content),
                        "raw_content": content,
                        "metadata": json.dumps({
                            "source_type": source_type,
                            "original_title": title,
                        }),
                    },
                )
                # Reconcile doc_id with the row actually persisted. On ON
                # CONFLICT DO UPDATE the surviving row keeps its OWN id; the
                # generated uuid was never inserted. Chunk INSERTs below FK to
                # this id, so adopt the returned one (fresh insert returns the
                # same uuid → no-op).
                _persisted_id = _doc_upsert.scalar()
                if _persisted_id is not None:
                    doc_id = _persisted_id
            await session.commit()

        # ── Build the cross-phase context. Stages mutate ``ctx`` in place;
        # ``ingest()`` stays a thin orchestrator (build → stages → return).
        ctx = _IngestCtx(
            record_bot_id=record_bot_id,
            title=title,
            content=content,
            source_url=source_url,
            source_type=source_type,
            language=language,
            mime_type=mime_type,
            existing_doc_id=existing_doc_id,
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_id,
            channel_type=channel_type,
            raw_bytes=raw_bytes,
            file_name=file_name,
            blocks=blocks,
            step_tracker=step_tracker,
            audit=_audit,
            audit_bot=_audit_bot,
            ingest_t0=_ingest_t0,
            doc_id=doc_id,
            is_reindex=is_reindex,
            content_hash=content_hash,
            parser_row_chunks=parser_row_chunks,
        )

        await self._stage_u3_clean(ctx)
        await self._stage_u4_chunk(ctx)
        await self._stage_u5_enrich(ctx)
        await self._stage_u6_vn_segment(ctx)

        # Read back stage outputs needed by the inline incremental-indexing
        # block (the ``_compute_chunk_hashes(enriched_chunks)`` call site is
        # pinned in ``_IngestMixin`` by tests/unit/test_chunk_hash_uses_enriched_text).
        enriched_chunks = ctx.enriched_chunks
        chunks = ctx.chunks
        _chunk_quality_skip_indices = ctx.chunk_quality_skip_indices

        parent_child_enabled = ctx.parent_child_enabled
        pc_hierarchy = ctx.pc_hierarchy

        # Incremental re-indexing: hash fingerprints the *enriched* text so a
        # changed enrichment context (e.g. updated document summary) forces
        # re-embed even when the raw chunk text is unchanged. Hashing raw
        # chunks here would skip the embedder and leave a stale vector
        # under a misleading content_hash.
        new_chunk_hashes: list[str] = self._compute_chunk_hashes(enriched_chunks)

        # Audit per-chunk previews — head + tail snippet + estimated tokens
        # so a leader can eyeball each chunk's boundary quality.
        if _audit is not None:
            _head = DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_HEAD
            _tail = DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_TAIL
            for _i, _txt in enumerate(chunks):
                # Token estimate: ~1 token per 4 chars (conservative; real
                # tokenisation would require tiktoken which is heavy here).
                _approx_tokens = max(1, len(_txt) // 4)
                _preview_head = _txt[:_head]
                _preview_tail = _txt[-_tail:] if len(_txt) > _head + _tail else ""
                await _audit.log(
                    _audit_bot,
                    "ingest",
                    "chunk_created",
                    {
                        "chunk_index": _i,
                        "len_chars": len(_txt),
                        "approx_tokens": _approx_tokens,
                        "preview_head": _preview_head,
                        "preview_tail": _preview_tail,
                        "content_hash": new_chunk_hashes[_i][:16],
                    },
                )

        # Check existing chunks (only relevant for re-index)
        existing_hashes: dict[int, str] = {}  # {chunk_index: content_hash}
        if is_reindex:
            async with session_with_tenant(
                self._sf, record_tenant_id=record_tenant_id,
            ) as session:
                result = await session.execute(
                    text("SELECT chunk_index, content_hash FROM document_chunks WHERE record_document_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                existing_hashes = {r[0]: r[1] for r in result.fetchall()}

        # Determine which chunks need (re-)embedding
        chunks_to_embed: list[tuple[int, str, str]] = []  # (index, text, hash)
        unchanged_indices: list[int] = []

        # Build parent index set for parent-child mode
        _pc_parent_indices: set[int] = set()
        if parent_child_enabled and pc_hierarchy:
            _pc_parent_indices = {
                item["chunk_index"] for item in pc_hierarchy if item["is_parent"]
            }

        for i, chunk_text in enumerate(chunks):
            # Skip low-quality chunks before embedding (chunk-quality gate).
            # When the feature flag is OFF the skip set is empty by
            # construction, so this branch is a no-op for the default path.
            if i in _chunk_quality_skip_indices:
                continue
            chunk_hash = new_chunk_hashes[i]
            if i in existing_hashes and existing_hashes[i] == chunk_hash:
                unchanged_indices.append(i)
            else:
                chunks_to_embed.append((i, chunk_text, chunk_hash))

        # Indices of old chunks that no longer exist (stale)
        stale_indices = [idx for idx in existing_hashes if idx >= len(chunks)]

        logger.info(
            "incremental_indexing",
            title=title,
            total=len(chunks),
            unchanged=len(unchanged_indices),
            to_embed=len(chunks_to_embed),
            stale=len(stale_indices),
            is_reindex=is_reindex,
        )

        # Diff-based re-ingest telemetry (T2-CostPerf).
        # The chunk-level hash diff is already computed above (incremental
        # indexing path). When ``diff_based_reingest_enabled`` is flipped on
        # we surface the saving as a dedicated structlog event so the
        # Master Observability Matrix can attribute cost-per-feature.
        # The compute is pure (zero DB); see ``shared/diff_reingest.py``.
        if is_reindex and self._cfg is not None:
            _diff_flag = bool(
                await self._cfg.get(
                    "diff_based_reingest_enabled",
                    DEFAULT_DIFF_REINGEST_ENABLED,
                )
            )
            if _diff_flag:
                _diff_cost_rate = float(
                    await self._cfg.get(
                        "embed_cost_usd_per_1m_tokens",
                        DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
                    )
                )
                _diff_result = _diff_reingest_compute(
                    enriched_chunks,
                    new_chunk_hashes,
                    existing_hashes,
                    cost_per_1m_tokens=_diff_cost_rate,
                )
                _diff_reingest_log_event(
                    _diff_result,
                    enabled=True,
                    record_bot_id=str(record_bot_id),
                    record_document_id=str(doc_id),
                    cost_per_1m_tokens=_diff_cost_rate,
                )

        ctx.new_chunk_hashes = new_chunk_hashes
        ctx.existing_hashes = existing_hashes
        ctx.chunks_to_embed = chunks_to_embed
        ctx.unchanged_indices = unchanged_indices
        ctx.stale_indices = stale_indices
        ctx.pc_parent_indices = _pc_parent_indices

        await self._stage_u7_embed_store(ctx)
        return await self._stage_finalize(ctx)


    async def _extract_graph_entities(
        self,
        *,
        bot_uuid: uuid.UUID,
        title: str,
        chunks_to_process: list[tuple[int, str]],
        rows_inserted: list[dict],
        record_tenant_id: uuid.UUID | None = None,
    ) -> None:
        """Background task: extract entity-relation triples for GraphRAG.

        Only runs if graph_rag_default_mode != "disabled" in system_config.
        Non-blocking — failures are logged but do not affect ingestion.
        """
        try:
            # Check if GraphRAG is enabled
            if self._cfg is None:
                return
            graph_mode = await self._cfg.get("graph_rag_default_mode", "disabled")
            if graph_mode == "disabled":
                return

            max_triples = await self._cfg.get_int("graph_rag_max_triples_per_chunk", 10)

            # Build a lightweight LLM callable for entity extraction
            import litellm as _litellm

            extraction_model = await self._cfg.get("graph_rag_entity_extraction_model", "")
            if not extraction_model:
                extraction_model = await self._cfg.get("llm_default_model", DEFAULT_METADATA_EXTRACTION_MODEL)

            kg_service = KnowledgeGraphService()

            # Create a minimal LLM adapter for the extraction
            class _MiniLLM:
                async def complete(self, _cfg: Any, messages: list[dict], **kwargs: Any) -> dict:
                    resp = await _litellm.acompletion(
                        model=extraction_model,
                        messages=messages,
                        temperature=kwargs.get("temperature", 0.0),
                        max_tokens=kwargs.get("max_tokens", DEFAULT_LLM_MAX_TOKENS),
                        timeout=DEFAULT_HTTP_TIMEOUT_S,
                    )
                    choice = resp.choices[0]
                    usage = resp.usage or type("U", (), {"prompt_tokens": 0, "completion_tokens": 0})()
                    return {
                        "text": choice.message.content or "",
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "cost_usd": 0.0,
                        "finish_reason": choice.finish_reason or "stop",
                    }

            class _MiniResolver:
                async def resolve_runtime(self, **_kw: Any) -> None:
                    return None

            mini_llm = _MiniLLM()
            mini_resolver = _MiniResolver()

            # Map chunk_index to chunk_id from inserted rows
            chunk_id_map: dict[int, uuid.UUID] = {}
            for row in rows_inserted:
                chunk_id_map[row["idx"]] = row["id"]

            total_triples = 0
            for chunk_idx, chunk_text in chunks_to_process:
                if not chunk_text.strip():
                    continue
                triples = await kg_service.extract_entities(
                    chunk_content=chunk_text,
                    document_name=title,
                    llm=mini_llm,
                    model_resolver=mini_resolver,
                    max_triples=max_triples,
                )
                if triples:
                    source_chunk_id = chunk_id_map.get(chunk_idx)
                    async with session_with_tenant(
                        self._sf, record_tenant_id=record_tenant_id,
                    ) as session:
                        inserted = await kg_service.store_triples(
                            record_bot_id=bot_uuid,  # audit L2-4: was bot_id= → TypeError → triples discarded
                            triples=triples,
                            session=session,
                            source_chunk_id=source_chunk_id,
                        )
                        await session.commit()
                        total_triples += inserted

            if total_triples > 0:
                logger.info(
                    "graph_rag_entities_extracted",
                    title=title,
                    bot_id=str(bot_uuid),
                    triples=total_triples,
                    chunks_processed=len(chunks_to_process),
                )

        except Exception:  # noqa: BLE001 — graph extraction is best-effort (LLM provider + DB); ingest must not fail if KG fails
            logger.warning(
                "graph_rag_extraction_failed",
                title=title,
                bot_id=str(bot_uuid),
                exc_info=True,
            )
