"""Resource-ownership pre-verify helpers.

Centralised guard so admin routes check ``record_tenant_id`` BEFORE mutate.
Without this layer a tenant admin who learns the UUID of another tenant's
binding could PATCH/DELETE through ``admin_ai`` since RBAC is tenant-agnostic.

Super-admin bypasses; non-existent or foreign bindings both yield 404 to avoid
leaking enumeration oracles.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from starlette.requests import Request

from ragbot.shared.constants import DEFAULT_SUPER_ADMIN_LEVEL
from ragbot.shared.rbac import check_min_level
from ragbot.shared.types import TenantId


async def require_binding_ownership(request: Request, binding_id: UUID) -> None:
    """Reject 404 when caller's tenant != binding's tenant.

    Super-admin bypasses to retain cross-tenant repair access.
    """
    if check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL):
        return

    caller_tid = getattr(request.state, "record_tenant_id", None)
    if caller_tid is None:
        raise HTTPException(status_code=404, detail="binding not found")

    repo = request.app.state.container.ai_config_repo()
    row = await repo.get_binding(binding_id, record_tenant_id=TenantId(caller_tid))
    if row is None:
        # Either does not exist OR belongs to another tenant — collapse to 404
        # so callers cannot enumerate foreign-tenant resource UUIDs.
        raise HTTPException(status_code=404, detail="binding not found")

    # Defense-in-depth only. The mutating repo paths now enforce the same
    # tenant filter atomically in the UPDATE WHERE clause (Issue #20), so
    # any TOCTOU race between this SELECT and the write returns rowcount=0
    # and surfaces as 404 upstream — no cross-tenant write can land.


__all__ = ["require_binding_ownership"]
