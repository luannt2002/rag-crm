"""Chat routes.

External identity is the 4-tuple ``(record_tenant_id: UUID,
workspace_id: str, bot_id: str, channel_type: str)``. The tenant UUID
is lifted onto ``request.state`` by the ``TenantContextMiddleware``
from the JWT bearer claim — never accepted from the request body. The
workspace slug arrives on the body and falls back to
``str(record_tenant_id)`` when the caller omits it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ragbot.application.commands.chat_commands import (
    AnswerQuestionCommand,
    GiveFeedbackCommand,
)
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.interfaces.http.schemas.chat_schema import (
    ChatAcceptedResponse,
    ChatRequest,
    FeedbackRequest,
)
from ragbot.shared.types import (
    BotId,
    ConversationId,
    MessageId,
    TenantId,
    TraceId,
    UserId,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a question (returns 202 + job_id)",
    dependencies=[Depends(require_permission_dep("chat", "submit"))],
)
async def submit_chat(req: ChatRequest, request: Request) -> ChatAcceptedResponse:
    container = request.app.state.container
    uc = container.answer_question_uc()

    # record_tenant_id sourced from JWT — middleware-bound; the request body
    # never carries the UUID (defence against caller-supplied tenant claims).
    record_tenant_id = request.state.record_tenant_id
    if record_tenant_id is None:
        raise HTTPException(status_code=403, detail="missing tenant context")

    # Body fallback: caller without a slug receives the tenant UUID.
    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_id,
    )

    # Resolve the 4-key identity → BotConfig.id (UUID).
    registry = container.bot_registry_service()
    bot_cfg = await registry.lookup(
        record_tenant_id, workspace_id, req.bot_id, req.channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail="bot_not_found")

    cmd = AnswerQuestionCommand(
        record_tenant_id=TenantId(record_tenant_id),
        record_bot_id=BotId(bot_cfg.id),
        workspace_id=workspace_id,
        bot_id=req.bot_id,
        channel_type=req.channel_type,
        user_id=UserId(req.user_id),
        conversation_id=None,
        content=req.content,
        channel="api",
        history_limit=req.history_limit,
        external_message_id=req.external_message_id,
        mode=req.mode,
        callback_url=req.callback_url,
        trace_id=TraceId(request.state.trace_id),
        received_at=datetime.now(tz=timezone.utc),
    )
    result = await uc.execute(cmd)
    return ChatAcceptedResponse(
        job_id=str(result.job_id),
        status="queued",
        status_url=result.status_url,
        trace_id=str(result.trace_id),
    )


@router.post(
    "/feedback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record user feedback (up/down) on an assistant message",
    dependencies=[Depends(require_permission_dep("chat", "feedback"))],
)
async def submit_feedback(req: FeedbackRequest, request: Request) -> dict[str, object]:
    container = request.app.state.container
    uc = container.give_feedback_uc()

    record_tenant_id = request.state.record_tenant_id
    if record_tenant_id is None:
        raise HTTPException(status_code=403, detail="missing tenant context")

    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_id,
    )

    # Same 4-key resolve as /chat — feedback targets a specific
    # (tenant, workspace, bot, channel) tuple, never an internal UUID
    # from the wire.
    registry = container.bot_registry_service()
    bot_cfg = await registry.lookup(
        record_tenant_id, workspace_id, req.bot_id, req.channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail="bot_not_found")

    cmd = GiveFeedbackCommand(
        record_tenant_id=TenantId(record_tenant_id),
        record_bot_id=BotId(bot_cfg.id),
        workspace_id=workspace_id,
        conversation_id=ConversationId(req.conversation_id),
        message_id=MessageId(req.message_id),
        rating=req.rating,
        comment=req.comment,
        user_id=UserId(req.user_id),
        trace_id=TraceId(request.state.trace_id),
    )
    await uc.execute(cmd)
    return {"ok": True, "trace_id": request.state.trace_id}


__all__ = ["router"]
