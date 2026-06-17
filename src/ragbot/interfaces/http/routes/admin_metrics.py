"""Admin metrics routes (v0.2.0 — Phần 5)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.constants import (
    DEFAULT_ADMIN_LEVEL,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)
from ragbot.shared.rbac import require_min_level
from ragbot.shared.types import BotId, TenantId

router = APIRouter(tags=["admin/metrics"])


def _require_admin(request: Request) -> None:
    require_min_level(request, DEFAULT_ADMIN_LEVEL)  # view-only metrics


@router.get(
    "/metrics/overview",
    dependencies=[Depends(require_permission_dep("system", "metrics_overview"))],
)
async def metrics_overview(
    request: Request,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    record_bot_id: UUID | None = Query(default=None),
) -> dict[str, object]:
    repo = request.app.state.container.request_log_repo()
    data = await repo.get_overview(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        date_from=date_from,
        date_to=date_to,
        record_bot_id=BotId(record_bot_id) if record_bot_id else None,
    )
    return {"ok": True, "data": data}


@router.get(
    "/metrics/by-model",
    dependencies=[Depends(require_permission_dep("system", "metrics_by_model"))],
)
async def metrics_by_model(
    request: Request,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict[str, object]:
    repo = request.app.state.container.request_log_repo()
    rows = await repo.get_metrics_by_model(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        date_from=date_from,
        date_to=date_to,
    )
    return {"ok": True, "data": rows}


@router.get(
    "/metrics/top-questions",
    dependencies=[
        Depends(require_permission_dep("system", "metrics_top_questions")),
    ],
)
async def metrics_top_questions(
    request: Request,
    only_failed: bool = Query(default=False),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, object]:
    repo = request.app.state.container.request_log_repo()
    rows = await repo.get_top_questions(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        limit=limit,
        only_failed=only_failed,
    )
    return {"ok": True, "data": list(rows)}


@router.get("/metrics/steps")
async def metrics_step_breakdown(
    request: Request,
    date_from: datetime | None = Query(default=None),
) -> dict[str, object]:
    _require_admin(request)
    repo = request.app.state.container.request_log_repo()
    rows = await repo.get_step_breakdown(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        date_from=date_from,
    )
    return {"ok": True, "data": rows}


@router.get("/metrics/active-models")
async def metrics_active_models(request: Request) -> dict[str, object]:
    _require_admin(request)
    repo = request.app.state.container.ai_config_repo()
    models = await repo.list_models(enabled_only=True)
    by_kind: dict[str, int] = {}
    for m in models:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
    return {
        "ok": True,
        "data": {"total_active": len(models), "by_kind": by_kind},
    }


__all__ = ["router"]
