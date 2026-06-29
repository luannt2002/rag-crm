"""Sync routes — upstream BE clients call these to sync bots + documents.

Covers: bot upsert, document sync/list/delete.

Identity: ``record_tenant_id`` UUID is lifted from the JWT bearer
(``request.state``); body legacy ``tenant_id`` INT is accepted from
upstream BE clients but resolved to UUID via
``tenants.config->>'upstream_tenant_id'`` before any DB write touches
the ``bots.record_tenant_id`` UUID FK column.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.application.services.chunk_context_enricher import ChunkContextEnricher
from ragbot.application.services.document_service import DocumentService
from ragbot.application.services.narrate_service import NarrateService
from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.infrastructure.llm.llm_chunk_context_provider import (
    LLMChunkContextProvider,
)
from ragbot.infrastructure.narrate.registry import build_narrate
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.bot_bindings import ensure_bot_bindings
from ragbot.shared.constants import (
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_SUPER_ADMIN_LEVEL,
    DEFAULT_SYNC_DOCUMENTS_WIPE_MODE,
    DEFAULT_TEMPERATURE,
    MAX_BOT_ID_LENGTH,
    MAX_BOT_NAME_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    MAX_TITLE_LENGTH,
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_ID_PATTERN,
)
from ragbot.shared.pagination import page_limit as _page_limit
from ragbot.shared.rbac import check_min_level
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


def _sys_config(request: Request) -> SystemConfigService:
    """Build a SystemConfigService from the DI container.

    @param request: FastAPI Request
    @return: SystemConfigService instance
    """
    container = request.app.state.container
    return SystemConfigService(
        session_factory=container.session_factory(),
        redis_client=container.redis_client(),
    )


def _caller_record_tenant(request: Request) -> uuid.UUID | None:
    """Lift ``record_tenant_id`` UUID from JWT-bound request.state.

    Returns ``None`` for platform-admin (super_admin) unscoped tokens —
    ``audit_log.record_tenant_id`` is nullable so the audit row is still
    written with NULL tenant when the caller is a platform admin.
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _resolve_body_tenant(
    request: Request, body_tenant_int: int,
) -> uuid.UUID:
    """Translate body-supplied legacy ``tenant_id`` INT → UUID FK.

    NestJS upstream still sends INT in the body during the migration
    window. Platform must (a) cross-check that INT against the JWT
    record_tenant_id (cross-tenant guard) and (b) resolve to the UUID
    FK that ``bots.record_tenant_id`` actually stores.
    """
    container = request.app.state.container
    sf = container.session_factory()
    async with sf() as session:
        row = await session.execute(
            text(
                "SELECT id FROM tenants "
                "WHERE (config->>'upstream_tenant_id')::int = :tid LIMIT 1"
            ),
            {"tid": int(body_tenant_int)},
        )
        scalar = row.scalar()
    if scalar is None:
        raise HTTPException(
            status_code=404,
            detail=f"upstream tenant_id={body_tenant_int} not registered",
        )
    resolved = uuid.UUID(str(scalar))
    # Cross-tenant guard: JWT record_tenant_id MUST equal resolved UUID
    # (super_admin bypass via `_caller_record_tenant` returning None).
    jwt_tenant = _caller_record_tenant(request)
    if (
        jwt_tenant is not None
        and not check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL)
        and jwt_tenant != resolved
    ):
        raise HTTPException(status_code=403, detail="record_tenant_id mismatch")
    return resolved


def _build_audit_entry(
    request: Request,
    *,
    action: str,
    resource_type: str,
    resource_id: str | uuid.UUID,
    before: dict | None,
    after: dict | None,
    record_bot_id: uuid.UUID | None = None,
    record_tenant_id: uuid.UUID | None = None,
) -> AuditEntry:
    """Build a forensic ``AuditEntry`` from the FastAPI request context.

    Every ``/sync/*`` mutation must emit an audit row.
    Upstream bulk ingest is a privileged operation; auditors
    require a per-call trail keyed by ``record_bot_id`` + actor token.
    Pass ``record_tenant_id`` explicitly when the route already
    resolved the body INT → UUID; falls back to caller JWT otherwise.
    """
    return AuditEntry(
        record_tenant_id=record_tenant_id or _caller_record_tenant(request),
        record_bot_id=record_bot_id,
        actor_user_id=getattr(request.state, "user_id", None) or "unknown",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,  # type: ignore[arg-type]
        before=before,
        after=after,
        reason=None,
        trace_id=getattr(request.state, "trace_id", "n/a"),
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SyncBotRequest(BaseModel):
    bot_id: str = Field(..., min_length=1, max_length=MAX_BOT_ID_LENGTH, description="External bot UID")
    channel_type: str = Field(..., min_length=1, max_length=MAX_CHANNEL_TYPE_LENGTH, description="Free-form channel identifier (e.g. zalo, messenger, api, web, ...)")
    bot_name: str = Field(..., min_length=1, max_length=MAX_BOT_NAME_LENGTH)
    tenant_id: int = Field(..., description="Tenant ID — REQUIRED to scope upsert and prevent cross-tenant identity collision")
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; legacy upstream payloads omit this and the "
            "route resolver falls back to str(record_tenant_id)."
        ),
    )
    system_prompt: str = ""
    temperature: float = Field(default=DEFAULT_TEMPERATURE, ge=0.0, le=2.0)
    max_tokens: int = Field(default=DEFAULT_GENERATION_MAX_TOKENS, ge=1, le=32000)


class SyncDocumentItem(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(min_length=1, description="Text content (already parsed)")
    url: str | None = None
    source_type: str | None = None


class SyncDocumentsRequest(BaseModel):
    # External identity required on the wire so we no longer have to
    # fish ``tenant_id_int`` out of request.state. Upstream sync
    # services (NestJS) MUST emit the legacy INT tenant_id; the slug
    # is optional and resolved to ``str(record_tenant_id)`` when absent.
    tenant_id: int = Field(
        ...,
        description="Tenant ID — REQUIRED external INT from upstream",
    )
    bot_id: str = Field(min_length=1, max_length=MAX_BOT_ID_LENGTH)
    channel_type: str = Field(min_length=1, max_length=MAX_CHANNEL_TYPE_LENGTH)
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; legacy upstream omits this and the route "
            "resolver falls back to str(record_tenant_id)."
        ),
    )
    documents: list[SyncDocumentItem] = Field(min_length=1)
    # Opt-in destructive wipe. Default OFF: replace only docs whose
    # ``url`` overlaps with the incoming payload. Setting True restores
    # the hard-wipe behaviour and requires super_admin.
    wipe_existing: bool = Field(
        default=DEFAULT_SYNC_DOCUMENTS_WIPE_MODE,
        description=(
            "If True, hard-delete ALL existing docs for this bot before "
            "ingest (super_admin only). If False (default), upsert by "
            "source_url — preserves docs not in the payload."
        ),
    )


class DeleteDocumentsRequest(BaseModel):
    tenant_id: int = Field(
        ...,
        description="Tenant ID — REQUIRED external INT from upstream",
    )
    bot_id: str = Field(min_length=1, max_length=MAX_BOT_ID_LENGTH)
    channel_type: str = Field(min_length=1, max_length=MAX_CHANNEL_TYPE_LENGTH)
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; legacy upstream omits this and the route "
            "resolver falls back to str(record_tenant_id)."
        ),
    )


# ---------------------------------------------------------------------------
# POST /ragbot/sync/bot — upsert bot
# ---------------------------------------------------------------------------
# RBAC note: upstream BE calls with a service JWT (role="service",
# level=60) — passes the admin-level gate naturally. Human callers need at
# least admin level. The fallback is the same Depends — no separate branch.
@router.post(
    "/bot",
    dependencies=[Depends(require_permission_dep("sync", "bot_upsert"))],
)
async def sync_bot(req: SyncBotRequest, request: Request) -> dict:
    """Upsert a bot: create when absent, update when it already exists.

    @param req: bot details (bot_id, channel_type, bot_name, ...)
    @return: {ok, action, bot_uuid, bot_id, channel_type}
    """
    container = request.app.state.container
    sf = container.session_factory()

    # Translate body legacy ``tenant_id`` INT → ``record_tenant_id`` UUID FK
    # and cross-tenant guard against the JWT bearer claim.
    record_tenant_uuid = await _resolve_body_tenant(request, req.tenant_id)
    workspace_slug = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_uuid,
    )

    async with sf() as session:
        # Check exists — MUST scope by record_tenant_id UUID + workspace slug
        # to prevent cross-tenant + cross-workspace collision (two tenants /
        # two workspaces can both pick bot_id="support").
        result = await session.execute(
            text("""
                SELECT id FROM bots
                WHERE bot_id = :bot_id
                  AND channel_type = :channel_type
                  AND record_tenant_id = :record_tenant_id
                  AND workspace_id = :workspace_id
                  AND is_deleted = false
            """),
            {
                "bot_id": req.bot_id,
                "channel_type": req.channel_type,
                "record_tenant_id": record_tenant_uuid,
                "workspace_id": workspace_slug,
            },
        )
        existing = result.fetchone()

        cfg_svc = _sys_config(request)
        default_temp = await cfg_svc.get_float("llm_default_temperature", 0.3)
        default_max_tok = await cfg_svc.get_int("llm_default_max_tokens", 450)
        default_top_p = await cfg_svc.get_float("llm_default_top_p", 0.4)

        setting_options = {
            "frequency_penalty": 0,
            "max_tokens": req.max_tokens if req.max_tokens != DEFAULT_GENERATION_MAX_TOKENS else default_max_tok,
            "response_format": "text",
            "presence_penalty": 0,
            "temperature": req.temperature if req.temperature != DEFAULT_TEMPERATURE else default_temp,
            "top_p": default_top_p,
        }

        # Get default model IDs. ai_models.kind uses "llm" for chat/generation
        # models (NOT "chat"); the prior 'chat' literal mismatched the schema so
        # the canonical B2B /sync onboarding path left every new bot without an
        # llm_primary binding → resolve_llm raised → first chat 500. Mirror the
        # corrected auto-pick in bot_admin_routes. No LIMIT: a 2-row cap could
        # return two llm rows and drop the embedding pick.
        model_id = None
        embedding_model_id = None
        result2 = await session.execute(
            text("SELECT id, kind FROM ai_models WHERE enabled = true AND kind IN ('llm', 'embedding')"),
        )
        for row in result2.fetchall():
            if row[1] == "llm" and model_id is None:
                model_id = row[0]
            elif row[1] == "embedding" and embedding_model_id is None:
                embedding_model_id = row[0]

        if existing:
            bot_uuid = existing[0]
            # record_tenant_id (UUID FK) in WHERE prevents accidental cross-tenant
            # update even if upstream call somehow provides mismatched id+tenant.
            # record_tenant_id is NOT in SET clause — bot's tenancy is immutable post-create.
            await session.execute(
                text("""
                    UPDATE bots SET
                        bot_name = :bot_name,
                        system_prompt = :system_prompt,
                        setting_options = CAST(:setting_options AS jsonb),
                        record_model_id = :model_id,
                        record_embedding_model_id = :embedding_model_id,
                        updated_at = now()
                    WHERE id = :id AND record_tenant_id = :record_tenant_id
                """),
                {
                    "id": bot_uuid,
                    "bot_name": req.bot_name,
                    "record_tenant_id": record_tenant_uuid,
                    "system_prompt": req.system_prompt,
                    "setting_options": json.dumps(setting_options),
                    "model_id": model_id,
                    "embedding_model_id": embedding_model_id,
                },
            )
            await session.commit()
            action = "updated"
        else:
            bot_uuid = uuid.uuid4()
            await session.execute(
                text("""
                    INSERT INTO bots (id, bot_id, channel_type, bot_name, record_tenant_id,
                        workspace_id, system_prompt, setting_options, record_model_id,
                        record_embedding_model_id, is_deleted)
                    VALUES (:id, :bot_id, :channel_type, :bot_name, :record_tenant_id,
                        :workspace_id, :system_prompt, CAST(:setting_options AS jsonb),
                        :model_id, :embedding_model_id, false)
                """),
                {
                    "id": bot_uuid,
                    "bot_id": req.bot_id,
                    "channel_type": req.channel_type,
                    "bot_name": req.bot_name,
                    "record_tenant_id": record_tenant_uuid,
                    "workspace_id": workspace_slug,
                    "system_prompt": req.system_prompt,
                    "setting_options": json.dumps(setting_options),
                    "model_id": model_id,
                    "embedding_model_id": embedding_model_id,
                },
            )
            await session.commit()
            action = "created"

    # Auto-create bot_model_bindings if missing
    if model_id or embedding_model_id:
        async with sf() as session:
            await ensure_bot_bindings(
                session, bot_uuid, model_id, embedding_model_id,
                record_tenant_id=record_tenant_uuid,
                temperature=req.temperature, max_tokens=req.max_tokens,
            )
            await session.commit()

    # Invalidate bot registry cache (tenant + workspace scoped).
    registry = container.bot_registry_service()
    await registry.invalidate(
        record_tenant_uuid, workspace_slug, req.bot_id, req.channel_type,
    )

    # Emit forensic audit row for every upsert.
    audit_repo = container.ai_config_repo()
    await audit_repo.write_audit(
        _build_audit_entry(
            request,
            action="bot_sync_upsert",
            resource_type="bot",
            resource_id=bot_uuid,
            before=None,
            after={
                "action": action,
                "bot_id": req.bot_id,
                "channel_type": req.channel_type,
                "upstream_tenant_id": req.tenant_id,
                "record_tenant_id": str(record_tenant_uuid),
                "bot_name": req.bot_name,
            },
            record_bot_id=bot_uuid if isinstance(bot_uuid, uuid.UUID) else None,
            record_tenant_id=record_tenant_uuid,
        ),
    )

    logger.info("sync_bot", action=action, bot_id=req.bot_id, channel_type=req.channel_type)
    return {
        "ok": True,
        "action": action,
        "bot_uuid": str(bot_uuid),
        "bot_id": req.bot_id,
        "channel_type": req.channel_type,
    }


# ---------------------------------------------------------------------------
# POST /ragbot/sync/documents — sync documents (chunk + embed + store)
# ---------------------------------------------------------------------------
@router.post(
    "/documents",
    dependencies=[Depends(require_permission_dep("sync", "documents_upsert"))],
)
async def sync_documents(req: SyncDocumentsRequest, request: Request) -> dict:
    """Sync documents for a bot: chunk, embed, store into document_chunks.

    @param req: {bot_id, channel_type, documents[]}
    @return: {ok, total_documents, total_chunks, documents}
    """
    container = request.app.state.container
    settings = request.app.state.settings

    # Translate body legacy INT → record_tenant_id UUID + cross-tenant guard.
    record_tenant_uuid = await _resolve_body_tenant(request, req.tenant_id)
    workspace_slug = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_uuid,
    )

    # 1. Find bot via repository — 4-key identity.
    repo = container.bot_repo()
    cfg = await repo.find_by_4key(
        record_tenant_uuid, workspace_slug, req.bot_id, req.channel_type,
    )
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Bot {req.bot_id}:{req.channel_type} not found. Sync bot first.")
    bot_uuid = cfg.id

    # 2. Replace existing (UPSERT) + ingest new via DocumentService.
    # Default: UPSERT — soft-delete only docs whose ``source_url`` is in
    # the incoming payload. ``wipe_existing=True`` restores the hard-wipe
    # behaviour and is super-admin gated.
    # AdapChunk Narrate-then-Embed stage (TABLE/FORMULA/IMAGE).
    # Build the LLM strategy inline (resolved via ``enrichment`` binding,
    # same pattern as document_worker). Falls back to NullNarrateGenerator
    # if config says provider="null" or any init step fails.
    from ragbot.shared.constants import (
        DEFAULT_NARRATE_PROVIDER,
        DEFAULT_NARRATE_THEN_EMBED_ENABLED,
    )
    _narrate_svc: Any = None
    try:
        _cfg_for_narrate = _sys_config(request)
        _narrate_provider_name = str(await _cfg_for_narrate.get(
            "narrate_provider", DEFAULT_NARRATE_PROVIDER,
        ))
        _narrate_enabled = await _cfg_for_narrate.get_bool(
            "narrate_then_embed_enabled", DEFAULT_NARRATE_THEN_EMBED_ENABLED,
        )
        if _narrate_provider_name == "llm":
            _narrate_spec = await container.model_resolver().resolve_llm(
                bot_uuid,
                record_tenant_id=record_tenant_uuid,
                intent="enrichment",  # type: ignore[arg-type]
            )
            _narrate_strategy = build_narrate(
                "llm",
                llm=container.llm(),
                spec=_narrate_spec,
                record_tenant_id=record_tenant_uuid,
                trace_id="sync-ingest",  # type: ignore[arg-type]
            )
        else:
            _narrate_strategy = build_narrate("null")
        _narrate_svc = NarrateService(
            strategy=_narrate_strategy, enabled=_narrate_enabled,
        )
    except Exception as exc:  # noqa: BLE001 — narrate is opt-in; failure must not break sync ingest
        logger.warning(
            "narrate_service_init_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        _narrate_svc = None
    _stats_repo_sync: Any = None
    if hasattr(container, "stats_index_repo"):
        try:
            _stats_repo_sync = container.stats_index_repo()
        except Exception:  # noqa: BLE001 — DI hook best-effort
            _stats_repo_sync = None
    # Anthropic-style Contextual Retrieval — per-call provider binds the LLM
    # + resolver to this sync's tenant/bot for chunk_context generation.
    _chunk_ctx_provider_sync = LLMChunkContextProvider(
        llm=container.llm(),
        model_resolver=container.model_resolver(),
        record_tenant_id=record_tenant_uuid,
        record_bot_id=bot_uuid,
    )
    _chunk_ctx_enricher_sync = ChunkContextEnricher(
        provider=_chunk_ctx_provider_sync,
    )
    doc_svc = DocumentService(
        session_factory=container.session_factory(),
        embedder=container.embedder(),
        settings=settings,
        config_service=_sys_config(request),
        model_resolver=container.model_resolver(),
        pii_redactor=container.pii(),
        bot_repo=container.bot_repo(),
        chunk_context_enricher=_chunk_ctx_enricher_sync,
        stats_index_repo=_stats_repo_sync,
        narrate_service=_narrate_svc,
        corpus_version_service=container.corpus_version_service(),
    )
    if req.wipe_existing:
        if not check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL):
            raise HTTPException(
                status_code=403,
                detail="wipe_existing=true requires super_admin",
            )
        await doc_svc.delete_all_for_bot(
            bot_uuid,
            record_tenant_id=record_tenant_uuid,
        )
    else:
        incoming_urls = [d.url or "" for d in req.documents]
        await doc_svc.replace_documents_for_bot(
            bot_uuid,
            source_urls=incoming_urls,
            record_tenant_id=record_tenant_uuid,
        )

    total_chunks = 0
    doc_results: list[dict[str, Any]] = []

    for doc_item in req.documents:
        result = await doc_svc.ingest(
            record_bot_id=bot_uuid,
            title=doc_item.title,
            content=doc_item.content,
            source_url=doc_item.url or "",
            source_type=doc_item.source_type or "sync",
            record_tenant_id=record_tenant_uuid,
        )
        total_chunks += result.chunks
        doc_results.append({
            "title": result.title,
            "document_id": str(result.document_id),
            "chunks": result.chunks,
            "embedded": result.embedded,
        })
        logger.info("sync_document", title=result.title, chunks=result.chunks, bot_id=req.bot_id)

    # Emit forensic audit row covering the bulk ingest.
    audit_repo = container.ai_config_repo()
    await audit_repo.write_audit(
        _build_audit_entry(
            request,
            action="document_bulk_ingest",
            resource_type="document",
            resource_id=bot_uuid,
            before=None,
            after={
                "bot_id": req.bot_id,
                "channel_type": req.channel_type,
                "upstream_tenant_id": req.tenant_id,
                "record_tenant_id": str(record_tenant_uuid),
                "total_documents": len(req.documents),
                "total_chunks": total_chunks,
                "document_ids": [d["document_id"] for d in doc_results],
                "wipe_existing": req.wipe_existing,
            },
            record_bot_id=bot_uuid,
            record_tenant_id=record_tenant_uuid,
        ),
    )

    return {
        "ok": True,
        "bot_id": req.bot_id,
        "channel_type": req.channel_type,
        "bot_uuid": str(bot_uuid),
        "total_documents": len(req.documents),
        "total_chunks": total_chunks,
        "documents": doc_results,
    }


# ---------------------------------------------------------------------------
# GET /ragbot/sync/documents?bot_id=...&channel_type=...
# ---------------------------------------------------------------------------
@router.get(
    "/documents",
    dependencies=[Depends(require_permission_dep("sync", "documents_list"))],
)
async def list_documents(
    bot_id: str,
    channel_type: str,
    request: Request,
    tenant_id: int = Query(..., ge=1, description="Tenant ID — REQUIRED 3-key identity"),
    limit: int | None = None,
) -> dict:
    """List a bot's documents, each with its chunk count.

    @param bot_id, channel_type: bot identity
    @param tenant_id: tenant scope — REQUIRED query param (3-key identity)
    @return: {ok, total, documents}

    ``tenant_id`` is a REQUIRED query parameter (FastAPI
    ``Query(..., ge=1)``) so external 3-key identity is mandatory at
    the wire; a missing value yields a 422 from FastAPI validation
    rather than a silent fallback to request state.
    """
    container = request.app.state.container
    sf = container.session_factory()
    # Translate query-supplied legacy INT → record_tenant_id UUID FK +
    # cross-tenant guard against JWT (super_admin bypass).
    record_tenant_uuid = await _resolve_body_tenant(request, tenant_id)

    async with sf() as session:
        result = await session.execute(
            text("""
                SELECT d.id, d.document_name, d.source_url, d.content_hash, d.created_at,
                    count(dc.id) as chunk_count
                FROM documents d
                JOIN bots b ON d.record_bot_id = b.id
                LEFT JOIN document_chunks dc ON dc.record_document_id = d.id
                WHERE b.bot_id = :bot_id AND b.channel_type = :channel_type
                    AND b.record_tenant_id = :record_tenant_id AND b.is_deleted = false
                    AND d.deleted_at IS NULL
                GROUP BY d.id
                ORDER BY d.created_at DESC
                LIMIT :lim
            """),
            {
                "bot_id": bot_id,
                "channel_type": channel_type,
                "record_tenant_id": record_tenant_uuid,
                "lim": _page_limit(limit),
            },
        )
        rows = result.fetchall()

    return {
        "ok": True,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "total": len(rows),
        "documents": [
            {
                "id": str(r[0]),
                "document_name": r[1],
                "source_url": r[2],
                "content_hash": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "chunk_count": r[5],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# DELETE /ragbot/sync/documents
# ---------------------------------------------------------------------------
@router.delete(
    "/documents",
    dependencies=[Depends(require_permission_dep("sync", "documents_delete"))],
)
async def delete_documents(req: DeleteDocumentsRequest, request: Request) -> dict:
    """Delete all documents and chunks for a bot.

    @param req: {bot_id, channel_type}
    @return: {ok, deleted_chunks, deleted_documents}
    """
    container = request.app.state.container
    # Translate body legacy INT → record_tenant_id UUID + cross-tenant guard.
    record_tenant_uuid = await _resolve_body_tenant(request, req.tenant_id)
    workspace_slug = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_uuid,
    )
    repo = container.bot_repo()
    cfg = await repo.find_by_4key(
        record_tenant_uuid, workspace_slug, req.bot_id, req.channel_type,
    )
    if cfg is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    doc_svc = DocumentService(
        session_factory=container.session_factory(),
        embedder=container.embedder(),
        settings=request.app.state.settings,
        config_service=_sys_config(request),
        model_resolver=container.model_resolver(),
        pii_redactor=container.pii(),
        bot_repo=container.bot_repo(),
        corpus_version_service=container.corpus_version_service(),
    )
    chunks_del, docs_del = await doc_svc.delete_all_for_bot(
        cfg.id,
        record_tenant_id=record_tenant_uuid,
    )

    # Emit forensic audit row for bulk delete.
    audit_repo = container.ai_config_repo()
    await audit_repo.write_audit(
        _build_audit_entry(
            request,
            action="document_bulk_delete",
            resource_type="document",
            resource_id=cfg.id,
            before={
                "deleted_chunks": int(chunks_del),
                "deleted_documents": int(docs_del),
            },
            after=None,
            record_bot_id=cfg.id,
            record_tenant_id=record_tenant_uuid,
        ),
    )

    return {"ok": True, "deleted_chunks": chunks_del, "deleted_documents": docs_del}


__all__ = ["router"]
