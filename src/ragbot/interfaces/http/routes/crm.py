"""CRM analytics routes — operator console read-layer (T2 cost/perf/obs).

Thin HTTP adapter over :class:`CrmAnalyticsService`. Each endpoint:

  1. enforces RBAC (``require_min_level``),
  2. lifts ``record_tenant_id`` from the JWT (``request.state``) — NEVER from
     the query/body,
  3. delegates the aggregation to the service.

Identity contract (CLAUDE.md): an admin (level 60) sees only its own tenant;
the cross-tenant view (``record_tenant_id is None``) is gated at the platform
super-admin level (100). The data layer additionally filters every query by
the resolved tenant, so a scoped caller can never read another tenant's rows.

Endpoints (mounted at ``{BASE}/crm``):
  * ``GET /crm/analytics/tokens``         — tokens + cost: timeline + rollup
  * ``GET /crm/analytics/latency``        — p50/p95/p99 per bot+channel
  * ``GET /crm/analytics/nodes``          — per-LangGraph-node bottleneck view
  * ``GET /crm/analytics/top-questions``  — top-N token-expensive (by hash)
  * ``GET /crm/analytics/quality``        — status / refusal / error / feedback
  * ``GET /crm/budget/status``            — token budgets vs current usage

Domain-neutral: counts / ratios / percentiles only — no brand or industry
literals. The dashboard UI lives at ``/static/crm.html``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from ragbot.application.services.crm_analytics_service import CrmAnalyticsService
from ragbot.shared.constants import (
    DEFAULT_CRM_MIN_OPERATOR_LEVEL,
    DEFAULT_CRM_MIN_SUPER_ADMIN_LEVEL,
)
from ragbot.shared.rbac import require_min_level

router = APIRouter(tags=["crm"])


def _resolve_scope(request: Request) -> UUID | None:
    """RBAC gate + tenant resolution shared by every CRM endpoint.

    Returns the caller's ``record_tenant_id`` (tenant-scoped view) or ``None``
    for a platform super-admin cross-tenant view. Admin level (60) is required
    in all cases; the cross-tenant view additionally requires super-admin (100).
    """
    require_min_level(request, DEFAULT_CRM_MIN_OPERATOR_LEVEL)
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        # No tenant binding → operator/platform token → cross-tenant rollup.
        require_min_level(request, DEFAULT_CRM_MIN_SUPER_ADMIN_LEVEL)
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="invalid tenant context") from exc


def _service(request: Request) -> CrmAnalyticsService:
    """Construct the analytics service from the DI container."""
    container = request.app.state.container
    return CrmAnalyticsService(session_factory=container.session_factory())


@router.get("/crm/analytics/tokens")
async def crm_tokens(
    request: Request,
    days: int | None = Query(default=None),
    bot_id: str | None = Query(default=None),
) -> dict:
    """Token + cost analytics: totals, daily timeline, workspace/bot rollup."""
    tid = _resolve_scope(request)
    data = await _service(request).tokens(
        record_tenant_id=tid, days=days, bot_id=bot_id,
    )
    return {"ok": True, **data}


@router.get("/crm/analytics/latency")
async def crm_latency(
    request: Request,
    days: int | None = Query(default=None),
    bot_id: str | None = Query(default=None),
) -> dict:
    """Latency p50/p95/p99 per (bot, channel)."""
    tid = _resolve_scope(request)
    data = await _service(request).latency(
        record_tenant_id=tid, days=days, bot_id=bot_id,
    )
    return {"ok": True, **data}


@router.get("/crm/analytics/nodes")
async def crm_nodes(
    request: Request,
    days: int | None = Query(default=None),
    bot_id: str | None = Query(default=None),
) -> dict:
    """Per-LangGraph-node latency/token breakdown (the bottleneck view)."""
    tid = _resolve_scope(request)
    data = await _service(request).nodes(
        record_tenant_id=tid, days=days, bot_id=bot_id,
    )
    return {"ok": True, **data}


@router.get("/crm/analytics/top-questions")
async def crm_top_questions(
    request: Request,
    days: int | None = Query(default=None),
    bot_id: str | None = Query(default=None),
    n: int | None = Query(default=None),
) -> dict:
    """Top-N token-expensive question groups (by question_hash — PII-safe)."""
    tid = _resolve_scope(request)
    data = await _service(request).top_questions(
        record_tenant_id=tid, days=days, bot_id=bot_id, n=n,
    )
    return {"ok": True, **data}


@router.get("/crm/analytics/quality")
async def crm_quality(
    request: Request,
    days: int | None = Query(default=None),
    bot_id: str | None = Query(default=None),
) -> dict:
    """Status distribution, refusal/error rate, feedback summary."""
    tid = _resolve_scope(request)
    data = await _service(request).quality(
        record_tenant_id=tid, days=days, bot_id=bot_id,
    )
    return {"ok": True, **data}


@router.get("/crm/budget/status")
async def crm_budget_status(request: Request) -> dict:
    """Active token budgets vs current-period usage."""
    tid = _resolve_scope(request)
    data = await _service(request).budget_status(record_tenant_id=tid)
    return {"ok": True, **data}
