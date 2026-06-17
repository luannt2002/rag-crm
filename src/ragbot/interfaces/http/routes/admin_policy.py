"""Admin tenant policy routes (v0.2.0 — Phần 8.2 / 8.5)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.constants import (
    DEFAULT_PRIVATE_DOC_RATIO,
    DEFAULT_QUALITY_SCORE,
    DEFAULT_TENANT_ADMIN_LEVEL,
)
from ragbot.shared.rbac import require_min_level
from ragbot.shared.types import BotId, TenantId

router = APIRouter(tags=["admin/policy"])


def _require_admin(request: Request) -> None:
    require_min_level(request, DEFAULT_TENANT_ADMIN_LEVEL)


class CapabilityUpsertRequest(BaseModel):
    model_id: UUID
    tier: str = "standard"
    can_web_search: bool = False
    can_read_private_docs: bool = True
    can_reasoning: bool = False
    can_tool_use: bool = False
    can_vision: bool = False
    quality_score: float = Field(default=DEFAULT_QUALITY_SCORE, ge=0.0, le=10.0)
    hallucination_rate: float = Field(default=0.0, ge=0.0, le=100.0)  # zero = disabled
    suitable_for: list[str] = Field(default_factory=list)
    not_suitable_for: list[str] = Field(default_factory=list)


class PolicyUpsertRequest(BaseModel):
    model_id: UUID
    bot_id: UUID | None = None
    private_doc_ratio: int = Field(default=DEFAULT_PRIVATE_DOC_RATIO, ge=0, le=100)
    web_search_ratio: int = Field(default=0, ge=0, le=100)  # zero = disabled
    general_knowledge_ratio: int = Field(default=0, ge=0, le=100)  # zero = disabled
    fallback_model_id: UUID | None = None
    default_for_task: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


@router.post(
    "/ai/models/{model_id}/capability",
    status_code=201,
    dependencies=[Depends(require_permission_dep("policy", "capability_upsert"))],
)
async def upsert_capability(
    model_id: UUID,
    req: CapabilityUpsertRequest,
    request: Request,
) -> dict[str, object]:
    if req.model_id != model_id:
        raise HTTPException(400, "model_id mismatch")
    repo = request.app.state.container.tenant_policy_repo()
    await repo.upsert_capability(
        record_model_id=model_id,
        tier=req.tier,
        can_web_search=req.can_web_search,
        can_read_private_docs=req.can_read_private_docs,
        can_reasoning=req.can_reasoning,
        can_tool_use=req.can_tool_use,
        can_vision=req.can_vision,
        quality_score=req.quality_score,
        hallucination_rate=req.hallucination_rate,
        suitable_for=req.suitable_for,
        not_suitable_for=req.not_suitable_for,
        updated_by=request.state.user_id or "unknown",
    )
    return {"ok": True}


@router.get("/ai/models/{model_id}/capability")
async def get_capability(model_id: UUID, request: Request) -> dict[str, object]:
    _require_admin(request)
    repo = request.app.state.container.tenant_policy_repo()
    cap = await repo.get_capability(record_model_id=model_id)
    return {"ok": True, "data": cap}


@router.get("/policies")
async def list_policies(
    request: Request,
    record_bot_id: UUID | None = None,
) -> dict[str, object]:
    _require_admin(request)
    repo = request.app.state.container.tenant_policy_repo()
    rows = await repo.list_policies(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        record_bot_id=BotId(record_bot_id) if record_bot_id else None,
    )
    return {"ok": True, "data": rows}


@router.post(
    "/policies",
    status_code=201,
    dependencies=[Depends(require_permission_dep("policy", "policy_upsert"))],
)
async def upsert_policy(
    req: PolicyUpsertRequest,
    request: Request,
) -> dict[str, object]:
    repo = request.app.state.container.tenant_policy_repo()
    policy_id = await repo.upsert_policy(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        record_model_id=req.model_id,
        record_bot_id=BotId(req.bot_id) if req.bot_id else None,
        private_doc_ratio=req.private_doc_ratio,
        web_search_ratio=req.web_search_ratio,
        general_knowledge_ratio=req.general_knowledge_ratio,
        record_fallback_model_id=req.fallback_model_id,
        default_for_task=req.default_for_task,
        enabled=req.enabled,
        actor_user_id=request.state.user_id or "unknown",
        trace_id=request.state.trace_id,
    )
    return {"ok": True, "policy_id": str(policy_id)}


__all__ = ["router"]
