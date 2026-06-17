"""Document routes.

Body carries 2-key bot identity ``(bot_id, channel_type)`` plus an
optional ``workspace_id``; tenant is lifted from the JWT bearer
(``request.state.record_tenant_id`` UUID). The route resolves to
``BotConfig.id`` (UUID) via ``BotRegistryService`` (4-key cached
lookup) before the typed command is built. ``BotId`` is
``NewType("BotId", UUID)``.

BE-to-BE retry safety: ``POST /documents/create`` honours the
``X-Idempotency-Key`` header. A repeated request within
:data:`DEFAULT_INGEST_IDEMPOTENCY_TTL_HOURS` returns 200 with the
original ``job_id`` (no second ingest queued). Missing header means
no idempotency contract; partner-side BE is responsible for retrying
safely when omitted.
"""

from __future__ import annotations

import structlog
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ragbot.application.commands.document_commands import (
    DeleteDocumentCommand,
    IngestDocumentCommand,
    RechunkByDocumentIdCommand,
    RechunkDocumentCommand,
)
from ragbot.application.services.ingest_idempotency_service import (
    canonical_request_hash,
)
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.interfaces.http.schemas.document_schema import (
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    IngestDocumentRequest,
    IngestDocumentResponse,
    RechunkByDocumentIdRequest,
    RechunkDocumentRequest,
)
from ragbot.shared.constants import (
    INGEST_IDEMPOTENCY_HEADER,
    INGEST_IDEMPOTENCY_KEY_MAX_LEN,
    INGEST_IDEMPOTENCY_STATE_DONE,
)
from ragbot.shared.types import BotId, TenantId, TraceId, WorkspaceId
from ragbot.interfaces.http._ingest_quota_guard import enforce_ingest_quota
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["documents"])


async def _resolve_bot_uuid(
    request: Request,
    *,
    record_tenant: UUID,
    workspace_id: WorkspaceId,
    bot_id: str,
    channel_type: str,
) -> BotId:
    """Resolve the 4-key identity → ``BotId`` UUID.

    Cached lookup (Redis + DB fallback). 404 on miss to keep the wire
    contract honest. Tenant authority is the JWT bearer claim, not
    request body — ``record_tenant`` lifted from ``request.state``.
    """
    registry = request.app.state.container.bot_registry_service()
    bot_cfg = await registry.lookup(
        record_tenant_id=record_tenant,
        workspace_id=workspace_id,
        bot_id=bot_id,
        channel_type=channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail="bot_not_found")
    return BotId(bot_cfg.id)


def _record_tenant(request: Request) -> UUID:
    """Lift ``record_tenant_id`` UUID from JWT-bound request state."""
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=403, detail="missing tenant context")
    return record_tenant


@router.post(
    "/documents/create",
    response_model=IngestDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a document (URL parse + chunk + embed + upsert)",
    dependencies=[Depends(require_permission_dep("document", "ingest"))],
)
async def ingest_document(
    req: IngestDocumentRequest, request: Request,
) -> IngestDocumentResponse:
    container = request.app.state.container
    uc = container.ingest_document_uc()
    record_tenant = _record_tenant(request)
    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant,
    )
    record_bot_id = await _resolve_bot_uuid(
        request,
        record_tenant=record_tenant,
        workspace_id=workspace_id,
        bot_id=req.bot_id,
        channel_type=req.channel_type,
    )

    # BE-to-BE retry safety. Header-only opt-in — when absent the
    # endpoint behaves identically to the pre-idempotency contract.
    idem_key = (request.headers.get(INGEST_IDEMPOTENCY_HEADER) or "").strip()
    if idem_key:
        if len(idem_key) > INGEST_IDEMPOTENCY_KEY_MAX_LEN:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"idempotency_key exceeds "
                    f"{INGEST_IDEMPOTENCY_KEY_MAX_LEN} chars"
                ),
            )
        # Canonical fingerprint of the request body — used so a partner
        # accidentally re-using the key with a different payload is
        # logged (still honours the first attempt; see service docs).
        body_hash = canonical_request_hash(
            req.model_dump_json(exclude_none=True),
        )
        idem_svc = container.ingest_idempotency_service()
        check = await idem_svc.check_and_record(
            record_tenant_id=record_tenant,
            workspace_id=workspace_id,
            idempotency_key=idem_key,
            request_hash=body_hash,
        )
        if check.is_duplicate:
            # Replay path — return the existing document instead of
            # queuing a second ingest. ``job_id`` carries the original
            # document_id when known so partners can poll the same
            # record. When the original is still ``"processing"`` the
            # field is empty; partners may retry shortly to pick up
            # the populated id.
            logger.info(
                "ingest_idempotency_replay",
                record_tenant_id=str(record_tenant),
                workspace_id=workspace_id,
                idem_key_prefix=idem_key[:12],
                existing_status=check.existing_status,
            )
            return IngestDocumentResponse(
                job_id=str(check.existing_doc_id or ""),
                tool_name="",
                status=(
                    check.existing_status or INGEST_IDEMPOTENCY_STATE_DONE
                ),
                trace_id=str(request.state.trace_id),
            )

    # Per-tenant daily ingest quota — charged AFTER the idempotency replay
    # short-circuit (a retry of an already-accepted upload must not be
    # double-charged) and BEFORE queuing, so a rejected upload never reaches
    # the worker pipeline (closes IQ-1; QuotaExceeded → HTTP 429).
    await enforce_ingest_quota(
        container, record_tenant_id=record_tenant, workspace_id=workspace_id,
    )

    cmd = IngestDocumentCommand(
        record_tenant_id=TenantId(record_tenant),
        record_bot_id=record_bot_id,
        workspace_id=workspace_id,
        source_url=req.source_url,
        document_name=req.document_name,
        mime_type=req.mime_type,
        language=req.language,
        authority_score=req.authority_score,
        uploaded_by=request.state.user_id,
        trace_id=TraceId(request.state.trace_id),
    )
    result = await uc.execute(cmd)
    return IngestDocumentResponse(
        job_id=str(result.job_id),
        tool_name=result.tool_name,
        status="queued",
        trace_id=str(result.trace_id),
    )


@router.delete(
    "/documents",
    response_model=DeleteDocumentResponse,
    summary="Delete by tool_name (sync) — bumps corpus_version",
    dependencies=[Depends(require_permission_dep("document", "delete_by_tool_name"))],
)
async def delete_document(
    req: DeleteDocumentRequest, request: Request,
) -> DeleteDocumentResponse:
    container = request.app.state.container
    uc = container.delete_document_uc()
    record_tenant = _record_tenant(request)
    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant,
    )
    record_bot_id = await _resolve_bot_uuid(
        request,
        record_tenant=record_tenant,
        workspace_id=workspace_id,
        bot_id=req.bot_id,
        channel_type=req.channel_type,
    )
    cmd = DeleteDocumentCommand(
        record_tenant_id=TenantId(record_tenant),
        record_bot_id=record_bot_id,
        workspace_id=workspace_id,
        tool_name=req.tool_name,
        trace_id=TraceId(request.state.trace_id),
    )
    result = await uc.execute(cmd)
    return DeleteDocumentResponse(
        deleted_chunks=result.deleted_chunks,
        corpus_version=int(result.corpus_version),
    )


@router.post(
    "/documents/rechunk",
    response_model=IngestDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-ingest existing document (delete chunks + enqueue ingest)",
    dependencies=[Depends(require_permission_dep("document", "rechunk"))],
)
async def rechunk_document(
    req: RechunkDocumentRequest, request: Request,
) -> IngestDocumentResponse:
    container = request.app.state.container
    uc = container.rechunk_document_uc()
    record_tenant = _record_tenant(request)
    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant,
    )
    record_bot_id = await _resolve_bot_uuid(
        request,
        record_tenant=record_tenant,
        workspace_id=workspace_id,
        bot_id=req.bot_id,
        channel_type=req.channel_type,
    )
    cmd = RechunkDocumentCommand(
        record_tenant_id=TenantId(record_tenant),
        record_bot_id=record_bot_id,
        workspace_id=workspace_id,
        source_url=req.source_url,
        trace_id=TraceId(request.state.trace_id),
    )
    result = await uc.execute(cmd)
    return IngestDocumentResponse(
        job_id=str(result.job_id),
        tool_name=result.tool_name,
        status="queued",
        trace_id=str(result.trace_id),
    )


@router.post(
    "/documents/rechunk-by-id",
    response_model=IngestDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary=(
        "Re-ingest a single document addressed by its UUID. "
        "Use when multiple documents share the same source_url."
    ),
    dependencies=[Depends(require_permission_dep("document", "rechunk"))],
)
async def rechunk_document_by_id(
    req: RechunkByDocumentIdRequest, request: Request,
) -> IngestDocumentResponse:
    """260525 Bug #2 endpoint — sibling of /documents/rechunk that
    addresses the document by primary key (no source_url disambiguation)."""
    container = request.app.state.container
    uc = container.rechunk_document_uc()
    record_tenant = _record_tenant(request)
    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant,
    )
    record_bot_id = await _resolve_bot_uuid(
        request,
        record_tenant=record_tenant,
        workspace_id=workspace_id,
        bot_id=req.bot_id,
        channel_type=req.channel_type,
    )
    cmd = RechunkByDocumentIdCommand(
        record_tenant_id=TenantId(record_tenant),
        record_bot_id=record_bot_id,
        workspace_id=workspace_id,
        document_id=req.document_id,
        trace_id=TraceId(request.state.trace_id),
    )
    result = await uc.execute_by_document_id(cmd)
    return IngestDocumentResponse(
        job_id=str(result.job_id),
        tool_name=result.tool_name,
        status="queued",
        trace_id=str(result.trace_id),
    )


__all__ = ["router"]
