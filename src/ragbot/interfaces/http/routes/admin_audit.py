"""Admin audit routes — chain audit + analytics endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.constants import DEFAULT_ADMIN_LEVEL
from ragbot.shared.rbac import require_min_level

router = APIRouter(tags=["admin/audit"])


def _require_admin_and_tenant(request: Request) -> UUID:
    """Enforce admin level + return the caller's record_tenant_id from JWT.

    Centralises the tenant scope: every audit query is locked to the
    caller's own tenant. Cross-tenant reads require super-admin
    impersonation, not this endpoint. ``TenantContextMiddleware``
    populates ``request.state.record_tenant_id`` as UUID.
    """
    require_min_level(request, DEFAULT_ADMIN_LEVEL)
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=401, detail="Missing tenant context")
    return record_tenant


@router.get(
    "/audit/messages/{message_id}",
    dependencies=[
        Depends(require_permission_dep("admin", "audit_message_read")),
    ],
)
async def audit_message(request: Request, message_id: int) -> dict[str, object]:
    """Return request_logs + request_steps + model_invocations khớp
    ``message_id`` (external ID), **scoped to caller's tenant**.

    Two independent gates: ``Depends`` enforces ``admin:audit_message_read``
    permission and the tenant claim from JWT scopes the repo query (without
    it any tenant-admin could iterate ``message_id`` across tenants).
    """
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=401, detail="Missing tenant context")
    logger = request.app.state.container.invocation_logger()
    data = await logger.fetch_by_message_id(
        message_id, record_tenant_id=record_tenant,
    )
    return {"ok": True, "data": data}


@router.get("/audit/overview")
async def audit_overview(
    request: Request,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    record_bot_id: UUID | None = Query(default=None),
) -> dict[str, object]:
    """Aggregate overview: document/chunk/query/token stats for auditor.

    Scoped to the caller's tenant via JWT; cross-tenant reads blocked.
    """
    record_tenant = _require_admin_and_tenant(request)
    repo = request.app.state.container.audit_repo()
    data = await repo.get_audit_overview(
        record_tenant_id=record_tenant,
        date_from=date_from,
        date_to=date_to,
        record_bot_id=record_bot_id,
    )
    return {"ok": True, "data": data}


@router.get("/audit/query-detail")
async def audit_query_detail(
    request: Request,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    record_bot_id: UUID | None = Query(default=None),
    cursor: datetime | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
) -> dict[str, object]:
    """Per-query breakdown with keyset pagination (tenant-scoped)."""
    record_tenant = _require_admin_and_tenant(request)
    repo = request.app.state.container.audit_repo()
    data = await repo.get_query_detail(
        record_tenant_id=record_tenant,
        date_from=date_from,
        date_to=date_to,
        record_bot_id=record_bot_id,
        cursor=cursor,
        limit=limit,
    )
    return {"ok": True, "data": data}


@router.get("/audit/verify")
async def audit_verify(
    request: Request,
    since: datetime | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=10000),
) -> dict[str, object]:
    """Recompute the ``audit_log`` hash chain for the caller's tenant.

    Returns ``{ok, total_rows_scanned, mismatches[], last_row_hash}``.
    A non-empty ``mismatches`` list signals tamper (UPDATE, DELETE or
    retroactive INSERT bypassing the immutable trigger). RBAC level-60.

    SEC-11 / alembic 010g.
    """
    record_tenant = _require_admin_and_tenant(request)
    verifier = request.app.state.container.audit_verifier()
    verdict = await verifier.verify_audit_chain(
        record_tenant_id=record_tenant,
        since=since,
        limit=limit,
    )
    return {
        "ok": verdict.ok,
        "data": {
            "total_rows_scanned": verdict.total_rows_scanned,
            "mismatch_count": len(verdict.mismatches),
            "mismatches": [
                {
                    "row_id": m.row_id,
                    "created_at": m.created_at,
                    "expected_hash": m.expected_hash,
                    "actual_hash": m.actual_hash,
                }
                for m in verdict.mismatches
            ],
            "last_row_hash": verdict.last_row_hash,
        },
    }


__all__ = ["router"]
