"""Admin metrics + usage-rollup routes over the token ledger.

Read-only cost/usage dashboard: time-bucketed usage timeseries, per-bot /
per-workspace / per-tenant token + cost roll-ups (tenant-scoped, RBAC >= admin)
and a cross-tenant leaderboard (RBAC level 100). All numbers come from
``token_ledger`` — the single per-call cost source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.constants import (
    DEFAULT_ADMIN_LEVEL,
    DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
    DEFAULT_CRM_WINDOW_DAYS,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SUPER_ADMIN_LEVEL,
    MAX_ANALYTICS_ALL_TENANTS_LIMIT,
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


@router.get("/metrics/usage/timeseries")
async def metrics_usage_timeseries(
    request: Request,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    group_by: str = Query(default="day"),       # hour | day | month
    breakdown: str = Query(default="none"),     # none | model | action | provider
    record_bot_id: UUID | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    scope: str = Query(default="tenant"),        # tenant | all
) -> dict[str, object]:
    """Log-center dashboard: token + cost over time from ``token_ledger``.

    Covers every external paid call (llm / rerank / embedding). ``scope=tenant``
    (RBAC >= admin) bounds to the caller's tenant; ``scope=all`` is the
    platform-wide view (RBAC level 100). Optional ``record_bot_id`` /
    ``workspace_id`` narrow to a single bot / workspace.
    """
    all_tenants = scope == "all"
    require_min_level(
        request,
        DEFAULT_SUPER_ADMIN_LEVEL if all_tenants else DEFAULT_ADMIN_LEVEL,
    )
    now = datetime.now(UTC)
    repo = request.app.state.container.token_ledger_analytics_repo()
    rows = await repo.usage_timeseries(
        record_tenant_id=request.state.record_tenant_id,
        date_from=date_from or (now - timedelta(days=DEFAULT_CRM_WINDOW_DAYS)),
        date_to=date_to or now,
        group_by=group_by,
        breakdown=breakdown,
        record_bot_id=record_bot_id,
        workspace_id=workspace_id,
        all_tenants=all_tenants,
    )
    return {
        "ok": True,
        "data": rows,
        "meta": {"group_by": group_by, "breakdown": breakdown, "scope": scope},
    }


@router.get("/metrics/usage/rollup")
async def metrics_usage_rollup(
    request: Request,
    dim: str = Query(default="bot"),         # bot | workspace | tenant
    breakdown: str = Query(default="none"),  # none | purpose | action | model | provider
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict[str, object]:
    """Tenant-scoped Σ tokens/cost roll-up over ``token_ledger``.

    Groups by ``dim`` (bot|workspace|tenant); each row carries CRM cardinality
    (``bot_count`` / ``workspace_count``) and ``turns`` (distinct request_id).
    Optional ``breakdown`` adds a second key (``purpose`` = per-purpose cost
    attribution). RBAC >= admin; always bounded to the caller's JWT tenant.
    """
    require_min_level(request, DEFAULT_ADMIN_LEVEL)
    now = datetime.now(UTC)
    repo = request.app.state.container.token_ledger_analytics_repo()
    rows = await repo.usage_rollup(
        record_tenant_id=request.state.record_tenant_id,
        date_from=date_from or (now - timedelta(days=DEFAULT_CRM_WINDOW_DAYS)),
        date_to=date_to or now,
        dim=dim,
        breakdown=breakdown,
    )
    return {
        "ok": True,
        "data": rows,
        "meta": {"dim": dim, "breakdown": breakdown},
    }


@router.get("/metrics/usage/cross-tenant")
async def metrics_usage_cross_tenant(
    request: Request,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(
        default=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
        ge=1,
        le=MAX_ANALYTICS_ALL_TENANTS_LIMIT,
    ),
) -> dict[str, object]:
    """Platform-wide per-tenant cost leaderboard (NO tenant filter).

    RBAC level 100 (super-admin) only — there is no tenant scoping. Returns one
    row per tenant with ``workspace_count`` / ``bot_count`` / ``turns`` + Σ
    tokens/cost, ordered by cost.
    """
    require_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL)
    now = datetime.now(UTC)
    repo = request.app.state.container.token_ledger_analytics_repo()
    rows = await repo.cross_tenant_rollup(
        date_from=date_from or (now - timedelta(days=DEFAULT_CRM_WINDOW_DAYS)),
        date_to=date_to or now,
        limit=limit,
    )
    return {"ok": True, "data": rows, "meta": {"limit": limit}}


__all__ = ["router"]
