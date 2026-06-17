"""Thumbs verdict feedback endpoint — scaffolding for the training-loop
analytics pipeline.

This route is **not yet wired** in :mod:`ragbot.interfaces.http.router`.
The active ``/api/ragbot/feedback`` endpoint lives in
:mod:`ragbot.interfaces.http.routes.chat` and writes the verdict back
into ``request_logs`` (per-request inline columns). Wiring this router
at the same path would collide; the admin who lands the parallel
``message_feedback`` analytics table also flips the registration on
``router.py``.

Identity contract is the same as :mod:`chat`:

* HTTP body carries the 2-key bot identity ``(bot_id, channel_type)``
  plus the optional ``workspace_id`` slug.
* JWT bearer claim populates ``request.state.record_tenant_id`` (UUID).
* Route resolves the 4-key tuple to ``record_bot_id`` via
  :class:`BotRegistryService`, then hands a single internal UUID to
  the repository.

The body's ``verdict`` field is one of the two thumbs constants
exposed in :mod:`ragbot.shared.constants`. The optional comment is
capped at :const:`MAX_FEEDBACK_COMMENT_LENGTH` characters; the route
declines longer payloads with a 422 from the pydantic validator.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.constants import (
    FEEDBACK_VERDICT_THUMBS_DOWN,
    FEEDBACK_VERDICT_THUMBS_UP,
    MAX_BOT_ID_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    MAX_FEEDBACK_COMMENT_LENGTH,
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_ID_PATTERN,
)
from ragbot.shared.workspace_id_validator import resolve_workspace_id

router = APIRouter(tags=["feedback"])


class ThumbsFeedbackRequest(BaseModel):
    """Body for ``POST /feedback/thumbs`` — thumbs verdict + optional comment.

    Body carries the 2-key bot identity + optional workspace slug.
    Tenant arrives via the JWT bearer claim, never the wire body.
    """

    model_config = ConfigDict(frozen=True)

    bot_id: str = Field(
        ..., min_length=1, max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug — opaque, RAG-agnostic.",
    )
    channel_type: str = Field(
        ..., min_length=1, max_length=MAX_CHANNEL_TYPE_LENGTH,
        description="Channel slug — e.g. 'web', 'zalo'.",
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; missing value falls back to "
            "str(record_tenant_id) at the route layer."
        ),
    )
    # External upstream message id — BIGINT, no ``record_`` prefix. Nullable
    # because a locally-generated message has no upstream wire id.
    message_id: int | None = Field(
        default=None,
        description="Upstream message id (BIGINT). Null when local-only.",
    )
    verdict: Literal["thumbs_up", "thumbs_down"]
    comment: str | None = Field(
        default=None, max_length=MAX_FEEDBACK_COMMENT_LENGTH,
    )


@router.post(
    "/feedback/thumbs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record a thumbs up/down verdict on an assistant message.",
    dependencies=[Depends(require_permission_dep("chat", "feedback"))],
)
async def submit_thumbs_feedback(
    req: ThumbsFeedbackRequest, request: Request,
) -> dict[str, object]:
    """Persist one thumbs verdict + optional comment.

    Returns ``{"ok": True, "feedback_id": "<uuid>"}`` on success. The
    repo write is RLS-scoped via ``session_with_tenant`` so a caller
    whose JWT carries tenant A cannot write into tenant B even if the
    body somehow names a bot from tenant B (the bot lookup would fail
    first; the RLS WITH CHECK is defence-in-depth).
    """
    container = request.app.state.container

    record_tenant_id = request.state.record_tenant_id
    if record_tenant_id is None:
        raise HTTPException(status_code=403, detail="missing tenant context")

    workspace_id = resolve_workspace_id(
        req.workspace_id, record_tenant_id=record_tenant_id,
    )

    # 4-key resolve → record_bot_id (UUID). Same contract as /chat and the
    # legacy /feedback in chat.py — a bot miss is a 404, not a 500.
    registry = container.bot_registry_service()
    bot_cfg = await registry.lookup(
        record_tenant_id, workspace_id, req.bot_id, req.channel_type,
    )
    if bot_cfg is None:
        raise HTTPException(status_code=404, detail="bot_not_found")

    # The pydantic Literal already constrained the verdict to the two
    # canonical values; we re-export the constants here so the wire
    # contract reads from the same SSoT as the repo + alembic.
    if req.verdict == FEEDBACK_VERDICT_THUMBS_UP:
        verdict_value = FEEDBACK_VERDICT_THUMBS_UP
    else:
        verdict_value = FEEDBACK_VERDICT_THUMBS_DOWN

    repo = container.message_feedback_repo()
    feedback_id = await repo.record(
        record_tenant_id=record_tenant_id,
        record_bot_id=bot_cfg.id,
        verdict=verdict_value,
        message_id=req.message_id,
        comment=req.comment,
    )

    return {"ok": True, "feedback_id": str(feedback_id)}


__all__ = ["ThumbsFeedbackRequest", "router"]
