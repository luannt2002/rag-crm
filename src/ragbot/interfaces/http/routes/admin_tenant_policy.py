"""Admin tenant policy CRUD routes — Replaces the manual SQL + ``pg_notify`` workflow for tweaking the three
P33 columns on ``tenants`` (``bypass_rate_limit``, ``rate_limit_per_min``,
``monthly_token_cap``). PATCH automatically invalidates the
``TenantConfigCache`` so the change takes effect on the next request
boundary.

RBAC:
- ``GET`` is gated by ``tenant:policy_read`` (level 80, tenant admin).
  Tenant admins may only read their own row; cross-tenant reads return
  404 (we surface tenant-not-found rather than 403 to avoid enumeration).
- ``PATCH`` is gated by ``tenant:policy_update`` (level 100, super admin).
  Rate-limit / token-cap flips are platform-wide trust changes, so
  tenant-level admins are intentionally barred.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.infrastructure.repositories.tenant_repository import TenantRepository
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.interfaces.http.schemas.admin_tenant_policy_schema import (
    TenantPolicyUpdateRequest,
)
from ragbot.shared.constants import DEFAULT_SUPER_ADMIN_LEVEL
from ragbot.shared.rbac import check_min_level

router = APIRouter(tags=["admin/tenant-policy"])


def _audit_payload(row: dict[str, object]) -> dict[str, object]:
    """Strip non-mutable / internal fields before persisting to audit_log.

    Drops ``tenant_id_int`` (internal mapping) and ``record_tenant_id`` /
    ``name`` (already on the audit row's resource_id + resource_type).
    Keeps the three mutable policy columns so before/after diffs are
    self-contained.
    """
    keep = {"bypass_rate_limit", "rate_limit_per_min", "monthly_token_cap"}
    return {k: v for k, v in row.items() if k in keep}


def _ensure_caller_can_read(request: Request, record_tenant_id: UUID) -> None:
    """Tenant admin reads its own row only; super_admin reads any row.

    Returns silently when the caller is allowed. Raises ``HTTPException(404)``
    on mismatch — we deliberately mirror "not found" rather than 403 so the
    endpoint cannot be used to enumerate tenant UUIDs.
    """
    if check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL):
        return
    caller_tid = getattr(request.state, "record_tenant_id", None)
    if caller_tid is None or str(caller_tid) != str(record_tenant_id):
        raise HTTPException(status_code=404, detail="tenant not found")


def _strip_internal(data: dict[str, object]) -> dict[str, object]:
    """Drop ``tenant_id_int`` from the response payload — internal only."""
    return {k: v for k, v in data.items() if k != "tenant_id_int"}


@router.get(
    "/tenants/{record_tenant_id}/policy",
    dependencies=[Depends(require_permission_dep("tenant", "policy_read"))],
)
async def get_tenant_policy(
    record_tenant_id: UUID, request: Request,
) -> dict[str, object]:
    """Return the current policy row (or 404 if the tenant does not exist)."""
    _ensure_caller_can_read(request, record_tenant_id)

    container = request.app.state.container
    sf = container.session_factory()
    async with sf() as session:
        repo = TenantRepository(session)
        data = await repo.get_policy(record_tenant_id)

    if data is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return {"ok": True, "data": _strip_internal(data)}


@router.patch(
    "/tenants/{record_tenant_id}/policy",
    dependencies=[Depends(require_permission_dep("tenant", "policy_update"))],
)
async def patch_tenant_policy(
    record_tenant_id: UUID,
    req: TenantPolicyUpdateRequest,
    request: Request,
) -> dict[str, object]:
    """PATCH the row and invalidate the per-tenant Redis cache."""
    container = request.app.state.container
    sf = container.session_factory()
    async with sf() as session:
        repo = TenantRepository(session)
        existing = await repo.get_policy(record_tenant_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="tenant not found")
        updated = await repo.update_policy(
            record_tenant_id,
            bypass_rate_limit=req.bypass_rate_limit,
            rate_limit_per_min=req.rate_limit_per_min,
            monthly_token_cap=req.monthly_token_cap,
        )

    if updated is None:
        # Race: row vanished between read + write. Treat as 404.
        raise HTTPException(status_code=404, detail="tenant not found")

    # Forensic audit — every PATCH on the three P33 policy columns is a
    # platform-trust change (rate-limit / token-cap bypasses can shift
    # cost + abuse posture). Mirror admin_ai's pattern: write through the
    # AI config repo's ``write_audit`` (the ``audit_log`` table is shared
    # across both routes since migration 0046's unification). Failure
    # here is NOT swallowed — auditors must see every PATCH or refuse
    # the change.
    audit_repo = container.ai_config_repo()
    caller_tid_raw = getattr(request.state, "record_tenant_id", None)
    caller_tid: UUID | None
    if isinstance(caller_tid_raw, UUID):
        caller_tid = caller_tid_raw
    elif caller_tid_raw is None:
        caller_tid = None
    else:
        try:
            caller_tid = UUID(str(caller_tid_raw))
        except (ValueError, TypeError):
            caller_tid = None
    await audit_repo.write_audit(
        AuditEntry(
            record_tenant_id=caller_tid,
            record_bot_id=None,
            actor_user_id=getattr(request.state, "user_id", None) or "unknown",
            action="tenant_policy_update",
            resource_type="tenant",
            resource_id=record_tenant_id,
            before=_audit_payload(existing),
            after=_audit_payload(updated),
            reason=None,
            trace_id=getattr(request.state, "trace_id", "n/a"),
        ),
    )

    # Cache is keyed by record_tenant_id UUID (canonical key).
    # ``record_tenant_id`` is already a UUID at this scope — reuse it
    # directly so invalidation hits the same key the cache wrote.
    cache = container.tenant_config_cache()
    await cache.invalidate(record_tenant_id)

    return {"ok": True, "data": _strip_internal(updated)}


__all__ = ["router"]
