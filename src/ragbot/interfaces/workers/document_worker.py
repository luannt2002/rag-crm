"""Document ingestion worker.

Consumes ``document.uploaded.v1``. Delegates all chunking/embedding/storage
to ``DocumentService.ingest()`` — this worker is a thin adapter that:

1. Receives message from event bus
2. Extracts doc_id, content, metadata
3. Calls ``document_service.ingest()``
4. Handles success/failure events + metrics
5. ACKs the message
"""

from __future__ import annotations

import asyncio
import hashlib
import signal
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import text as _sql_text

from ragbot.application.services import google_link_service
from ragbot.application.services.chunk_context_enricher import ChunkContextEnricher
from ragbot.application.services.document_service import DocumentService
from ragbot.application.services.narrate_service import NarrateService
from ragbot.application.services.step_tracker import StepTracker
from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.bootstrap import Container
from ragbot.config.logging import (
    bind_request_context,
    clear_request_context,
    mode_ctx,
    record_bot_id_ctx,
    setup_logging,
)
from ragbot.config.settings import get_settings
from ragbot.domain.events.document_events import DocumentFailed, DocumentIngested
from ragbot.infrastructure.llm.llm_chunk_context_provider import (
    LLMChunkContextProvider,
)
from ragbot.infrastructure.narrate.registry import build_narrate
from ragbot.infrastructure.observability.metrics import (
    document_ingest_duration_seconds,
    document_ingest_total,
)
from ragbot.infrastructure.parser.registry import (
    build_parser,
    detect_parser,
    detect_parser_robust,
)
from ragbot.shared.constants import (
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_NARRATE_PROVIDER,
    DEFAULT_NARRATE_THEN_EMBED_ENABLED,
    DEFAULT_VLM_CAPTION_PROMPT,
    DEFAULT_VLM_PROVIDER,
    SUBJECT_DOCUMENT_UPLOADED,
)
from ragbot.shared.errors import (
    EmbeddingError,
    ExternalServiceError,
    IngestError,
    WorkspaceIdInvalid,
)
from ragbot.shared.types import (
    BotId,
    CorpusVersion,
    DocumentId,
    EmbeddingModelVersion,
    JobId,
    TenantId,
    TraceId,
    WorkspaceId,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)


class _LocalSourceNotRefetchable(Exception):
    """Raised when a non-http(s) source (``local://``) has no stored content
    to reuse and therefore cannot be (re)fetched."""


def _is_refetchable_url(url: str) -> bool:
    """True only for http(s) URLs an HTTP client / OCR engine can fetch.

    A locally-uploaded file is stored under a ``local://<bot>/<uuid>`` pseudo-URL
    that is NOT fetchable — its content lives only in ``documents.raw_content``.
    """
    return (url or "").strip().lower().startswith(("http://", "https://"))


# DLC-2 transient classification (single source of truth): a 429 / 5xx / network
# fault is recoverable, so the message is RE-RAISED for bus XCLAIM-redelivery and
# the BE-to-BE idempotency row is LEFT at ``"processing"`` (the row TTL is the
# backstop). Everything else (ValueError / TypeError / KeyError = malformed
# payload) is terminal — retrying just burns budget, so the row is marked
# ``failed`` for a stable partner-visible state. Used by BOTH the re-raise gate
# and the idempotency lifecycle marking so the two can never disagree.
_TRANSIENT_INGEST_ERRORS: tuple[type[BaseException], ...] = (
    ExternalServiceError,
    EmbeddingError,
    IngestError,
    ConnectionError,
    TimeoutError,
    OSError,
    httpx.HTTPError,
)


def _is_transient_ingest_error(exc: BaseException) -> bool:
    """True when *exc* is a recoverable ingest failure (retry-worthy)."""
    return isinstance(exc, _TRANSIENT_INGEST_ERRORS)


def _ingest_idempotency_service(container: Container) -> Any | None:
    """Resolve the BE-to-BE idempotency service, or None when unavailable.

    The lifecycle marking is opt-in: only the upload that carried an
    ``X-Idempotency-Key`` queued a payload with ``idempotency_key``. A
    container without the provider (older test harness) degrades to None so
    the marking is skipped rather than crashing the ingest.
    """
    if not hasattr(container, "ingest_idempotency_service"):
        return None
    try:
        return container.ingest_idempotency_service()
    except Exception:  # noqa: BLE001 — DI hook best-effort; missing provider must not break ingest
        return None


async def handle_document_uploaded(payload: dict[str, Any], container: Container) -> None:
    """Process document.uploaded event — delegate to DocumentService.ingest().

    @param payload: event data (tenant_id, bot_id, document_id, ...)
    @param container: DI container

    P17 P1-4: bind_request_context is paired with clear_request_context
    in a try/finally so trace_id / tenant_id / bot_id cannot leak from
    one consumed message into the next (contextvars are process-scoped
    and workers reuse the same coroutine).
    """
    bind_request_context(
        trace_id=payload.get("trace_id", ""),
        record_tenant_id=payload.get("record_tenant_id"),
        bot_id=payload.get("record_bot_id"),
    )
    # Token-ledger flow tag — every LLM call during ingest (CR enrichment +
    # narrate) is recorded with mode='ingest' (no model-name guessing) +
    # record_bot_id (internal UUID, the report key).
    mode_ctx.set("ingest")
    record_bot_id_ctx.set(str(payload.get("record_bot_id") or ""))
    try:
        await _handle_document_uploaded_inner(payload, container)
    finally:
        clear_request_context()


async def _try_build_vlm_image_parser(
    container: Container,
    *,
    bot_id: Any,
    tenant_id: Any,
    trace_id: Any,
    mime_type: str,
) -> Any | None:
    """Build the VLM image parser for an image upload — or None (fall back to OCR).

    Gated by ``system_config.vlm_provider`` (default ``"null"`` = OFF). Resolves a
    vision spec; if no vision-capable model is available the function degrades to None
    (the legacy OCR path) rather than crashing the ingest — a missing capability must
    not break the document. Returns a ready ``VlmImageParser`` only when VLM is enabled
    AND the resolved model has ``supports_vision``.
    """
    if not (mime_type or "").lower().startswith("image/"):
        return None
    try:
        _cfg = SystemConfigService(
            session_factory=container.session_factory(),
            redis_client=container.redis_client(),
        )
        provider = str(await _cfg.get("vlm_provider", DEFAULT_VLM_PROVIDER))
        if provider == "null":
            return None
        spec = await container.model_resolver().resolve_llm(
            UUID(str(bot_id)),
            record_tenant_id=UUID(str(tenant_id)),
            intent="enrichment",  # type: ignore[arg-type]
        )
        if not getattr(spec, "supports_vision", False):
            logger.warning(
                "vlm_image_no_vision_model",
                model=getattr(spec, "model_name", "?"),
                detail="vlm_provider enabled but resolved model lacks vision; OCR fallback",
            )
            return None
        # Caption instruction is operator/owner-owned config (sacred #10: the
        # application never hardcodes the prompt text). Domain-neutral default.
        caption_prompt = str(
            await _cfg.get("vlm_caption_prompt", DEFAULT_VLM_CAPTION_PROMPT)
        )
        return build_parser(
            provider,
            llm=container.llm(),
            spec=spec,
            record_tenant_id=UUID(str(tenant_id)),
            trace_id=str(trace_id) if trace_id else "ingest",
            prompt=caption_prompt,
        )
    except (AttributeError, ValueError, TypeError, KeyError) as exc:
        # Resolver / config / construction surfaces — degrade to OCR, never crash ingest.
        logger.warning("vlm_image_parser_build_failed", error=str(exc))
        return None


async def _handle_document_uploaded_inner(payload: dict[str, Any], container: Container) -> None:
    logger.info("document.uploaded.consumed", payload_keys=list(payload.keys()))

    tenant_id = TenantId(UUID(payload["record_tenant_id"]))
    bot_id = BotId(UUID(payload["record_bot_id"]))
    document_id = DocumentId(UUID(payload["document_id"]))
    job_id = JobId(UUID(payload["job_id"]))
    trace_id = TraceId(payload["trace_id"])
    source_url = payload["source_url"]
    tool_name = payload["tool_name"]
    mime_type = payload.get("mime_type", "application/octet-stream")
    # BE-to-BE idempotency key (DLC-1): present only when the upload carried an
    # ``X-Idempotency-Key`` header. Empty/missing → lifecycle marking skipped.
    idempotency_key = (payload.get("idempotency_key") or "").strip()

    # Slug carried on the queued event mirrors the bot row at publish
    # time; missing values fall back via the central resolver
    # (``str(record_tenant_id)``). Malformed payloads land on the same
    # fallback rather than failing the ingest — the resolver re-validates
    # on the way through anyway.
    try:
        workspace_slug: WorkspaceId = resolve_workspace_id(
            payload.get("workspace_id"), record_tenant_id=tenant_id,
        )
    except WorkspaceIdInvalid:
        logger.warning(
            "document_worker_invalid_workspace_id",
            raw=str(payload.get("workspace_id"))[:64],
        )
        workspace_slug = resolve_workspace_id(None, record_tenant_id=tenant_id)

    # Feed the resolved slug to the RLS workspace GUC binder (ADR-W1-D3).
    bind_request_context(workspace_id=str(workspace_slug))

    job_repo = container.job_repo()
    settings = container.settings()
    clock = container.clock()

    await job_repo.update_status(job_id, record_tenant_id=tenant_id, status="running")

    # ── Phase D ingest observability — construct StepTracker per ingest job ──
    # The 7 U1-U7 wraps inside ``DocumentService.ingest()`` need a tracker
    # to emit ``request_steps`` rows. The parent ``request_logs`` row uses
    # the ingest-job UUID as ``request_id`` (FK target), ``connect_id="ingest"``
    # as a sentinel so chat-only dashboards can filter it out, and a
    # synthetic ``message_id`` derived from the ingest job for join-keys.
    # Failures here MUST NOT block the ingest itself — the tracker stays
    # ``None`` and U1-U7 wraps fall through to the no-op path.
    step_tracker: StepTracker | None = None
    _ingest_request_id = uuid4()
    try:
        request_log_repo = container.request_log_repo()
        # ``message_id`` for ingest jobs: BIGINT non-null DB constraint;
        # use the lowest 8 bytes of the job_id UUID so the value is stable
        # for analytics joins back to ``jobs.id``.
        _ingest_msg_id = int.from_bytes(
            hashlib.sha256(str(job_id).encode()).digest()[:8],
            byteorder="big",
            signed=False,
        ) & 0x7FFFFFFFFFFFFFFF  # mask to positive BIGINT
        await request_log_repo.create_request_log(
            request_id=_ingest_request_id,
            record_tenant_id=tenant_id,
            workspace_id=workspace_slug,
            connect_id="ingest",
            record_bot_id=bot_id,
            message_id=_ingest_msg_id,
            trace_id=str(trace_id),
            question_hash=hashlib.sha256(
                f"ingest:{document_id}".encode(),
            ).hexdigest(),
            channel_type=payload.get("channel_type"),
        )
        # D2 universal PII coverage — best-effort bot_cfg lookup so the
        # tracker can mask PII in step metadata. Failure degrades silent
        # (tracker still tracks, just without the universal mask).
        _ingest_bot_cfg: Any | None = None
        try:
            _ingest_bot_cfg = await container.bot_repo().get_by_id(
                record_tenant_id=tenant_id, record_bot_id=bot_id,
            )
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            logger.debug(
                "ingest_bot_cfg_lookup_failed",
                doc_id=str(document_id),
                error_type=type(exc).__name__,
            )
        step_tracker = StepTracker(
            request_id=_ingest_request_id,
            record_tenant_id=tenant_id,
            repo=request_log_repo,
            kind="ingest",
            metrics=container.metrics_port(),
            pii_redactor=container.pii(),
            bot_cfg=_ingest_bot_cfg,
            record_bot_id=bot_id,
        )
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        # Tracker is observability-only — never fail the ingest job because
        # the parent request_log row could not be persisted.
        logger.warning(
            "phase_d_ingest_tracker_init_failed",
            doc_id=str(document_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        step_tracker = None

    _ingest_t0 = time.perf_counter()
    try:
        # 1. Reuse raw_content fetched by endpoint (Action 1) — DO NOT
        # refetch source_url. Refetch causes Google Sheets URL
        # `docs.google.com/spreadsheets/d/.../edit?gid=` to receive an
        # HTML login interstitial (no auth header), which the parser then
        # treats as document content. Net result: chunks full of "Sign
        # in to Google" noise instead of sheet data — silent garbage in
        # production (Bug BLOCKER #1, audit 2026-05-13).
        full_text = ""
        # Structure-aware Block stream (ADR-W3-D1 S1). Only the OCR/kreuzberg
        # fallback path surfaces typed Blocks today; the registry path keeps
        # its row-dict side-channel, so this stays None there.
        parsed_blocks: list[Any] | None = None
        # Reuse stored raw_content ONLY for a non-refetchable (local://) source —
        # there it is the only copy. A refetchable Google URL is ALWAYS re-fetched
        # + re-parsed below via to_export_url (docx/csv → structured markdown), so a
        # flat pre-stored body (e.g. a Doc fetched as txt for the upload "has-data"
        # probe) can never bypass the structure-aware parser. Removes the fetch-path
        # divergence between the upload probe and the worker.
        if document_id and not _is_refetchable_url(source_url):
            try:
                sf = container.session_factory()
                async with sf() as session:
                    row = await session.execute(
                        _sql_text(
                            "SELECT raw_content FROM documents "
                            "WHERE id = :id AND deleted_at IS NULL",
                        ),
                        {"id": UUID(str(document_id))},
                    )
                    fetched = row.fetchone()
                    if fetched and fetched[0]:
                        full_text = fetched[0]
                        logger.info(
                            "worker_reused_raw_content",
                            document_id=str(document_id),
                            chars=len(full_text),
                        )
            except Exception as exc:  # noqa: BLE001 — DB best-effort
                logger.warning(
                    "worker_raw_content_lookup_failed_fallback_to_parse",
                    document_id=str(document_id),
                    error=str(exc),
                )

        # Fallback: fetch source_url and parse via the registry parser first
        # (CSV / Excel / Sheets / DOCX / MD → structured markdown), falling
        # through to OCR only when no parser matches the (mime_type, ext) pair
        # or the parser yields no chunks. The registry path keeps a Google
        # ``edit?gid=`` viewer URL off the OCR engine (which would OCR the HTML
        # login page to zero blocks).
        parsed_language: str | None = None
        # Document name drives parser ext-detection + file_name; lift from the
        # payload so the structured parsers run for their mime types.
        _doc_name = payload.get("document_name") or ""
        if not full_text.strip():
            # A locally-uploaded file is stored under a ``local://`` pseudo-URL
            # that no HTTP client / OCR engine can fetch. When its raw_content
            # is already gone (e.g. a prior wipe), there is nothing to refetch
            # — attempting it raised "unsupported protocol 'local://'" and
            # left the document stuck. Guard both fetch paths so only real
            # http(s) sources are refetched; otherwise fail with a clear,
            # actionable error instead of a cryptic protocol crash.
            _fetchable = _is_refetchable_url(source_url)
            registry_routed = False
            try:
                if not _fetchable:
                    raise _LocalSourceNotRefetchable(source_url)

                # Google Docs/Sheets viewer URL (``.../edit?gid=N``) → direct
                # export URL so the fetch receives structured txt/csv, not an
                # HTML login page. Setting mime/name routes it to the csv/sheets
                # parser instead of OCR — fixes the retry-storm where an HTML
                # viewer page parsed to empty text and looped to DLQ.
                _export_url = google_link_service.to_export_url(source_url)
                if _export_url != source_url:
                    source_url = _export_url
                    if "format=csv" in _export_url:
                        mime_type, _doc_name = "text/csv", (_doc_name or "sheet.csv")
                    elif "format=docx" in _export_url:
                        mime_type = (
                            "application/vnd.openxmlformats-"
                            "officedocument.wordprocessingml.document"
                        )
                        _doc_name = _doc_name or "doc.docx"
                    else:
                        mime_type, _doc_name = "text/plain", (_doc_name or "doc.txt")

                _ext = ""
                if _doc_name and "." in _doc_name:
                    _ext = _doc_name[_doc_name.rfind("."):].lower()

                # VLM image branch (multimodal Phase 2): an image upload is captioned
                # by a vision model when ``vlm_provider`` is enabled, else falls through
                # to the legacy OCR path. The VLM parser needs injected llm+spec, so it
                # is built here explicitly (detect_parser's no-arg probe cannot).
                parser = await _try_build_vlm_image_parser(
                    container, bot_id=bot_id, tenant_id=tenant_id,
                    trace_id=trace_id, mime_type=mime_type or "",
                )
                # Fetch the body BEFORE type-detection so the detection can
                # byte-sniff the real bytes — the same canonical order
                # DocumentService uses (declared mime/ext first, then sniff the
                # body on a miss; see document_service __init__.py + registry
                # detect_parser_robust). Fetching first lets a URL whose body
                # arrives as octet-stream / no-ext (e.g. a ``?download`` PDF
                # link) still route to its structured parser instead of
                # silently dropping to flat OCR.
                # follow_redirects: Google Docs ``export?format=docx`` returns
                # a 307 to a googleusercontent.com host; without following it
                # raise_for_status() turns the redirect into an HTTPStatusError
                # and the ingest dies (doc stuck DRAFT, 0 chunks).
                async with httpx.AsyncClient(
                    timeout=DEFAULT_HTTP_TIMEOUT_S, follow_redirects=True,
                ) as cli:
                    _resp = await cli.get(source_url)
                    _resp.raise_for_status()
                    _raw = _resp.content
                if parser is None:
                    # detect_parser_robust = detect_parser + byte-sniff fallback
                    # (registry.py). NOT plain detect_parser — which returns None
                    # for octet-stream/no-ext bodies and would drop them to flat
                    # OCR. Pass the module ``detect_parser`` so BOTH the declared
                    # and the sniffed lookup use the registry, mirroring the
                    # DocumentService canonical path.
                    parser = detect_parser_robust(
                        mime_type or "", _ext, _raw, detector=detect_parser,
                    )
                if parser is not None:
                    _chunks = await parser.parse(
                        _raw, file_name=_doc_name or "doc",
                    )
                    if _chunks:
                        full_text = "\n\n".join(
                            c["content"] for c in _chunks if c.get("content")
                        )
                        registry_routed = True
                        logger.info(
                            "worker_parser_registry_routed",
                            provider=(
                                parser.get_provider_name()
                                if hasattr(parser, "get_provider_name")
                                else "unknown"
                            ),
                            mime_type=mime_type,
                            file_ext=_ext,
                            chunks=len(_chunks),
                            bytes=len(_raw),
                            doc_id=str(document_id),
                        )
            except Exception as exc:  # noqa: BLE001 — registry is best-effort; fall through to OCR
                logger.warning(
                    "worker_parser_registry_failed_fallback_ocr",
                    mime_type=mime_type,
                    doc_id=str(document_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            if not registry_routed and not full_text.strip() and _fetchable:
                # OCR fallback — for PDFs, scanned docs, images, anything
                # the registry can't structurally parse. Only for refetchable
                # http(s) sources (a local:// upload has no fetchable URL).
                ocr = container.ocr()
                parsed = await ocr.parse(source_url, mime_type_hint=mime_type)
                # Keep the structure-aware Block stream alongside the flat
                # text (ADR-W3-D1 S1): ``content`` stays the fallback, but
                # ``blocks`` lets ingest carry the parser's type/atomicity
                # end-to-end instead of re-detecting it from markdown.
                parsed_blocks = list(parsed.blocks)
                full_text = "\n\n".join(b.content for b in parsed.blocks)
                parsed_language = parsed.language
        if not full_text.strip():
            if not _is_refetchable_url(source_url):
                # Clear, actionable failure for a locally-uploaded file whose
                # stored content is gone — re-fetch is impossible by design.
                raise RuntimeError(
                    "local upload has no stored raw_content and cannot be "
                    f"re-fetched (source_url={source_url[:60]}) — re-upload the file",
                )
            raise RuntimeError("empty document text after parse")

        # 2. Delegate to DocumentService.ingest()
        _cfg_svc = SystemConfigService(
            session_factory=container.session_factory(),
            redis_client=container.redis_client(),
        )
        # Source-URL allow-list validator (T1-Safety). Wired
        # best-effort — older containers without the ``source_validator``
        # provider fall back to None (passthrough) so existing test
        # harnesses keep working unchanged.
        _src_validator: Any = None
        if hasattr(container, "source_validator"):
            try:
                _src_validator = container.source_validator()
            except Exception:  # noqa: BLE001 — DI hook is best-effort; missing provider must not break ingest
                _src_validator = None
        # AdapChunk Tầng 6 — Narrate-then-Embed (TABLE/FORMULA/IMAGE).
        # Build the LLM strategy inline (no Container provider yet — the
        # registry is keyed by ``narrate_provider`` from system_config).
        # ``"null"`` → identity (NullNarrateGenerator), ``"llm"`` → routes
        # raw LaTeX/table content through the answer-path LLM to produce a
        # natural-language sentence used as the embed target. Output is
        # storage-only (Quality Gate #10 / HALLU=0 sacred).
        # ┌─ NANO-IN-INGEST PATH #3 of 3 — DEFAULT OFF (system_config
        # │  narrate_then_embed_enabled=false, alembic 0230) ────────────────────
        # │  WHY OFF: narrate turns each table/LaTeX block into a nano-generated
        # │  sentence before embedding — one nano call PER table block, which is
        # │  the spreadsheet ingest storm (a tabular sheet = hundreds of blocks).
        # │  With Jina late_chunking the raw structured cells embed with context,
        # │  so narrate is redundant for retrieval. Kept config-reversible. The
        # │  in-code DEFAULT (DEFAULT_NARRATE_THEN_EMBED_ENABLED) stays True only
        # │  as the fallback when no system_config row exists; the seeded row
        # │  (0230) is the source of truth = OFF. Re-enable ONLY without Jina.
        # │  Sibling nano paths: CR (#1) + enrich (#2) in ingest_stages_enrich.
        # └──────────────────────────────────────────────────────────────────────
        _narrate_svc: Any = None
        try:
            _narrate_provider_name = str(await _cfg_svc.get(
                "narrate_provider", DEFAULT_NARRATE_PROVIDER,
            )) if _cfg_svc is not None else DEFAULT_NARRATE_PROVIDER
            _narrate_enabled = await _cfg_svc.get_bool(
                "narrate_then_embed_enabled", DEFAULT_NARRATE_THEN_EMBED_ENABLED,
            ) if _cfg_svc is not None else DEFAULT_NARRATE_THEN_EMBED_ENABLED
            if _narrate_provider_name == "llm":
                _narrate_spec = await container.model_resolver().resolve_llm(
                    UUID(str(bot_id)),
                    record_tenant_id=UUID(str(tenant_id)),
                    intent="enrichment",  # type: ignore[arg-type]
                )
                _narrate_strategy = build_narrate(
                    "llm",
                    llm=container.llm(),
                    spec=_narrate_spec,
                    record_tenant_id=UUID(str(tenant_id)),
                    trace_id=str(trace_id) if trace_id else "ingest",  # type: ignore[arg-type]
                )
            else:
                _narrate_strategy = build_narrate("null")
            _narrate_svc = NarrateService(
                strategy=_narrate_strategy, enabled=_narrate_enabled,
            )
        except Exception as exc:  # noqa: BLE001 — narrate is opt-in feature; failure must not break ingest
            logger.warning(
                "narrate_service_init_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            _narrate_svc = None
        _stats_repo = None
        if hasattr(container, "stats_index_repo"):
            try:
                _stats_repo = container.stats_index_repo()
            except Exception:  # noqa: BLE001 — DI hook best-effort; missing provider must not break ingest
                _stats_repo = None
        # Anthropic-style Contextual Retrieval: per-doc enricher binds
        # the LLM + resolver to this ingest's tenant/bot so the chunk_context
        # column gets populated. Adapter raises only the answer-path LLM —
        # storage-only output (Quality Gate #10 / HALLU=0 sacred).
        _chunk_ctx_provider = LLMChunkContextProvider(
            llm=container.llm(),
            model_resolver=container.model_resolver(),
            record_tenant_id=UUID(str(tenant_id)),
            record_bot_id=UUID(str(bot_id)),
        )
        _chunk_ctx_enricher = ChunkContextEnricher(provider=_chunk_ctx_provider)
        doc_service = DocumentService(
            session_factory=container.session_factory(),
            embedder=container.embedder(),
            settings=settings,
            config_service=_cfg_svc,
            model_resolver=container.model_resolver(),
            pii_redactor=container.pii(),
            bot_repo=container.bot_repo(),
            source_validator=_src_validator,
            chunk_context_enricher=_chunk_ctx_enricher,
            stats_index_repo=_stats_repo,
            narrate_service=_narrate_svc,
            corpus_version_service=container.corpus_version_service(),
        )

        doc_name = payload.get("document_name", tool_name)
        result = await doc_service.ingest(
            record_bot_id=UUID(str(bot_id)),
            title=doc_name,
            content=full_text,
            source_url=source_url,
            source_type="worker",
            language=parsed_language or "auto",
            mime_type=mime_type,
            existing_doc_id=UUID(str(document_id)),
            record_tenant_id=UUID(str(tenant_id)),
            workspace_id=workspace_slug,
            blocks=parsed_blocks,
            step_tracker=step_tracker,
        )

        # 3. Publish success event
        corpus_version = CorpusVersion(1)
        uow_factory = container.uow_factory()
        async with uow_factory() as uow:
            await uow.add_outbox(
                DocumentIngested(
                    occurred_at=clock.now(),
                    record_tenant_id=tenant_id,
                    trace_id=trace_id,
                    workspace_id=workspace_slug,
                    job_id=job_id,
                    record_bot_id=bot_id,
                    document_id=document_id,
                    tool_name=tool_name,
                    chunk_count=result.chunks,
                    # Record-of-truth: the strategy the document was actually
                    # chunked with, surfaced from the ingest pipeline rather
                    # than a hardcoded literal.
                    strategy_used=result.strategy_used,
                    corpus_version=corpus_version,
                    embedding_model_version=EmbeddingModelVersion(
                        settings.embedding.model_version,
                    ),
                ),
            )
            await uow.commit()

        await job_repo.update_status(
            job_id, record_tenant_id=tenant_id, status="success",
            result={
                "document_id": str(document_id),
                "chunk_count": result.chunks,
                "corpus_version": 1,
            },
        )
        # DLC-1: stamp the BE-to-BE idempotency row ``done`` + bind the
        # document UUID so a partner retry within the TTL short-circuits to
        # the original document instead of re-ingesting. Best-effort — a
        # successful ingest must never be reported as failed because the
        # idempotency store hiccuped.
        if idempotency_key:
            _idem = _ingest_idempotency_service(container)
            if _idem is not None:
                try:
                    await _idem.mark_done(
                        record_tenant_id=UUID(str(tenant_id)),
                        workspace_id=str(workspace_slug),
                        idempotency_key=idempotency_key,
                        record_document_id=UUID(str(document_id)),
                    )
                except Exception:  # noqa: BLE001 — idempotency marking is best-effort
                    logger.warning(
                        "ingest_idempotency_mark_done_failed",
                        doc_id=str(document_id),
                        exc_info=True,
                    )
        try:
            document_ingest_total.labels(status="success").inc()
            document_ingest_duration_seconds.observe(time.perf_counter() - _ingest_t0)
        except Exception:  # noqa: BLE001
            pass

    except Exception as exc:  # noqa: BLE001
        logger.exception("document_ingest_failed", doc_id=str(document_id), bot_id=str(bot_id))
        # Surface the ingest failure to the configured webhook channel
        # — fire-and-forget, hook itself swallows any scheduling error
        # so this never blocks the failure-event outbox write below.
        try:
            _hook = container.error_notify_hook()
            await _hook.on_ai_error(
                error=exc,
                component="ingest.pipeline",
                record_tenant_id=tenant_id,
                record_bot_id=bot_id,
                request_id=None,
            )
        except Exception:  # noqa: BLE001 — alert path must not break ingest failure persistence
            logger.warning("document_error_notify_hook_failed", exc_info=True)
        await job_repo.update_status(
            job_id, record_tenant_id=tenant_id, status="failed", error=str(exc),
        )
        uow_factory = container.uow_factory()
        async with uow_factory() as uow:
            await uow.add_outbox(
                DocumentFailed(
                    occurred_at=datetime.now(tz=UTC),
                    record_tenant_id=tenant_id,
                    trace_id=trace_id,
                    workspace_id=workspace_slug,
                    job_id=job_id,
                    record_bot_id=bot_id,
                    document_id=document_id,
                    stage="ingest",
                    error_code="INGEST_FAILED",
                    error_message=str(exc),
                ),
            )
            await uow.commit()
        try:
            document_ingest_total.labels(status="failed").inc()
            document_ingest_duration_seconds.observe(time.perf_counter() - _ingest_t0)
        except Exception:  # noqa: BLE001
            pass

        _transient = _is_transient_ingest_error(exc)

        # DLC-1 / DLC-2: a TERMINAL failure marks the BE-to-BE idempotency row
        # ``failed`` so a partner retry sees a stable state — never stuck
        # ``"processing"``. A TRANSIENT failure is LEFT at ``"processing"`` so
        # the bus redelivery can still mark it ``done`` on a later success; the
        # row TTL is the backstop if every retry exhausts. Best-effort.
        if idempotency_key and not _transient:
            _idem = _ingest_idempotency_service(container)
            if _idem is not None:
                try:
                    await _idem.mark_failed(
                        record_tenant_id=UUID(str(tenant_id)),
                        workspace_id=str(workspace_slug),
                        idempotency_key=idempotency_key,
                    )
                except Exception:  # noqa: BLE001 — idempotency marking is best-effort
                    logger.warning(
                        "ingest_idempotency_mark_failed_failed",
                        doc_id=str(document_id),
                        exc_info=True,
                    )

        # HIGH #2 fix (audit 2026-05-13): re-raise TRANSIENT failures so
        # bus skips XACK → recover_pending_messages XCLAIMs the message
        # and retries up to dead-letter. Terminal errors (ValueError /
        # TypeError / KeyError = malformed payload) stay swallowed —
        # retrying them just burns rate-limit budget.
        if _transient:
            raise


async def main() -> None:
    """Start document ingestion worker — subscribe document.uploaded.v1."""
    settings = get_settings()
    setup_logging(level=settings.observability.log_level, json=True)
    container = Container()

    bus = container.bus()
    await bus.ensure_streams()

    stop = asyncio.Event()

    async def _handler(event: Any) -> None:
        # Z2-P0-2 fix: re-raise so the bus skips XACK on handler failure;
        # recover_pending_messages will XCLAIM and retry until the handler
        # writes a terminal job status, or dead-letter after 5 attempts.
        await handle_document_uploaded(event.payload, container)

    sub = await bus.subscribe(
        SUBJECT_DOCUMENT_UPLOADED,
        _handler,
        durable_name="document-worker",
        queue_group="documents",
    )

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    logger.info("document_worker_started")
    await stop.wait()
    await sub.unsubscribe()
    await bus.close()


if __name__ == "__main__":
    asyncio.run(main())
