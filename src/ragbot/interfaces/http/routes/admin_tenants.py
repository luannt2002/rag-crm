"""Admin tenant CRUD routes — super-admin only.

Endpoints
---------
- ``POST   /admin/tenants``                — create
- ``GET    /admin/tenants``                — paginated list (search by name)
- ``GET    /admin/tenants/{tenant_id}``    — read single
- ``PATCH  /admin/tenants/{tenant_id}``    — update name/config/policy
- ``DELETE /admin/tenants/{tenant_id}``    — soft-delete (409 if has bots)

RBAC
----
All endpoints require RBAC level 100 (super-admin / platform admin).
Tenant-level admins (level 80) intentionally cannot manage *tenants* —
that operation is platform-trust. Gate enforced via ``require_min_level``
inside each handler (no metadata-driven dep needed because there is only
one role gate; parity with ``admin_gdpr.py``).

Audit
-----
Every mutation (create / update / delete) emits an ``audit_log`` row via
the shared ``ai_config_repo.write_audit`` (the unified audit table since
migration 0046). Failures bubble up — auditors must see every change.

Cache
-----
Update / delete invalidate the per-tenant ``TenantConfigCache`` so the
flip takes effect on the next request boundary.

3-key identity
--------------
Tenant CRUD operates strictly on ``record_tenant_id`` UUID. The routes
do NOT take ``bot_id`` / ``channel_type`` (those are bot-identity, not
tenant-identity) per the CLAUDE.md naming convention.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.infrastructure.repositories.tenant_repository import (
    TenantHasActiveBotsError,
    TenantRepository,
    TenantSlugConflictError,
)
from ragbot.interfaces.http.schemas.admin_tenants import (
    TenantCreateRequest,
    TenantPatchRequest,
)
from ragbot.shared.constants import (
    DEFAULT_ADMIN_TENANT_LIST_LIMIT_DEFAULT,
    DEFAULT_ADMIN_TENANT_LIST_LIMIT_MAX,
    DEFAULT_SUPER_ADMIN_LEVEL,
)
from ragbot.shared.rbac import require_min_level

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin/tenants"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _require_super_admin(request: Request) -> None:
    """Guard — only platform-level callers may operate the tenants table."""
    require_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL)


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", None) or "unknown"


def _trace(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or "n/a"


def _caller_record_tenant(request: Request) -> UUID | None:
    """Extract the caller's *own* record_tenant_id for audit attribution.

    Differs from the path UUID — the path is the *target* tenant; this
    helper returns who is acting on it. Falls back to ``None`` when the
    JWT carried no tenant claim (super-admin tokens often omit it).
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce UUID + datetime fields to JSON-safe strings."""
    out = dict(row)
    if isinstance(out.get("record_tenant_id"), UUID):
        out["record_tenant_id"] = str(out["record_tenant_id"])
    for k in ("created_at", "updated_at", "deleted_at"):
        v = out.get(k)
        if v is not None and not isinstance(v, str):
            out[k] = v.isoformat()
    return out


async def _emit_audit(
    request: Request,
    *,
    target_tenant_id: UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Write a forensic audit row through the shared AI config repo.

    Mirrors ``admin_tenant_policy``'s pattern: use ``ai_config_repo``
    because ``audit_log`` was unified in migration 0046. Audit failures
    are NOT swallowed — auditors must see every mutation.
    """
    container = request.app.state.container
    audit_repo = container.ai_config_repo()
    await audit_repo.write_audit(
        AuditEntry(
            record_tenant_id=_caller_record_tenant(request),
            record_bot_id=None,
            actor_user_id=_actor(request),
            action=action,
            resource_type="tenant",
            resource_id=target_tenant_id,
            before=before,
            after=after,
            reason=None,
            trace_id=_trace(request),
        ),
    )


async def _invalidate_cache(request: Request, record_tenant_id: UUID) -> None:
    """Best-effort cache bust — Redis blip must NOT fail the route.

    The next reader will hit DB and re-warm. ``TenantConfigCache.invalidate``
    already swallows ``RedisError`` internally, but we wrap defensively in
    case a future cache impl re-raises.
    """
    try:
        cache = request.app.state.container.tenant_config_cache()
        await cache.invalidate(record_tenant_id)
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        logger.warning(
            "admin_tenant_cache_invalidate_failed",
            record_tenant_id=str(record_tenant_id),
            error_type=type(exc).__name__,
            err=str(exc),
        )


def _strip_audit_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Drop volatile timestamps from audit before/after diffs.

    ``created_at`` / ``updated_at`` change on every PATCH and would
    pollute the diff; ``deleted_at`` is captured separately by the
    delete action label. Keep the mutation-relevant fields only.
    """
    drop = {"created_at", "updated_at", "deleted_at", "record_tenant_id"}
    return {k: v for k, v in row.items() if k not in drop}


# ── Routes ─────────────────────────────────────────────────────────────────


@router.post("/tenants", status_code=201)
async def admin_create_tenant(
    req: TenantCreateRequest, request: Request,
) -> dict[str, Any]:
    """Create a tenant row and emit an audit event."""
    _require_super_admin(request)

    container = request.app.state.container
    sf = container.session_factory()
    try:
        async with sf() as session:
            repo = TenantRepository(session)
            row = await repo.create_tenant(
                name=req.name,
                slug=req.slug,
                config=req.config,
                upstream_tenant_id=req.upstream_tenant_id,
            )
    except TenantSlugConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        logger.exception(
            "admin_tenant_create_failed",
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="tenant create failed") from exc

    await _emit_audit(
        request,
        target_tenant_id=row["record_tenant_id"],
        action="admin_tenant_create",
        before=None,
        after=_strip_audit_fields(row),
    )

    return {"ok": True, "data": _serialize(row)}


@router.get("/tenants/{record_tenant_id}")
async def admin_get_tenant(
    record_tenant_id: UUID, request: Request,
) -> dict[str, Any]:
    """Return a single tenant or 404."""
    _require_super_admin(request)

    container = request.app.state.container
    sf = container.session_factory()
    async with sf() as session:
        repo = TenantRepository(session)
        row = await repo.get_tenant(record_tenant_id)

    if row is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return {"ok": True, "data": _serialize(row)}


@router.get("/tenants")
async def admin_list_tenants(
    request: Request,
    limit: int = Query(
        default=DEFAULT_ADMIN_TENANT_LIST_LIMIT_DEFAULT,
        ge=1,
        le=DEFAULT_ADMIN_TENANT_LIST_LIMIT_MAX,
        description="Page size (capped by DEFAULT_ADMIN_TENANT_LIST_LIMIT_MAX).",
    ),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(
        default=None,
        max_length=DEFAULT_ADMIN_TENANT_LIST_LIMIT_MAX,
        description="Optional case-insensitive name substring.",
    ),
) -> dict[str, Any]:
    """Paginated list of live (non-deleted) tenants."""
    _require_super_admin(request)

    container = request.app.state.container
    sf = container.session_factory()
    async with sf() as session:
        repo = TenantRepository(session)
        items, total = await repo.list_tenants(
            limit=limit, offset=offset, search=search,
        )

    return {
        "ok": True,
        "data": {
            "items": [_serialize(r) for r in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    }


@router.patch("/tenants/{record_tenant_id}")
async def admin_update_tenant(
    record_tenant_id: UUID,
    req: TenantPatchRequest,
    request: Request,
) -> dict[str, Any]:
    """PATCH name / config / rate-limit / token-cap; invalidate cache."""
    _require_super_admin(request)

    container = request.app.state.container
    sf = container.session_factory()
    try:
        async with sf() as session:
            repo = TenantRepository(session)
            outcome = await repo.update_tenant(
                record_tenant_id,
                name=req.name,
                config=req.config,
                bypass_rate_limit=req.bypass_rate_limit,
                rate_limit_per_min=req.rate_limit_per_min,
                monthly_token_cap=req.monthly_token_cap,
                allowed_origins=req.allowed_origins,
            )
    except TenantSlugConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if outcome is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    before, after = outcome

    await _emit_audit(
        request,
        target_tenant_id=record_tenant_id,
        action="admin_tenant_update",
        before=_strip_audit_fields(before),
        after=_strip_audit_fields(after),
    )

    await _invalidate_cache(request, record_tenant_id)

    return {"ok": True, "data": _serialize(after)}


@router.delete("/tenants/{record_tenant_id}", status_code=204)
async def admin_delete_tenant(
    record_tenant_id: UUID, request: Request,
) -> None:
    """Soft-delete the tenant. Returns 204 on success, 409 if active bots."""
    _require_super_admin(request)

    container = request.app.state.container
    sf = container.session_factory()
    try:
        async with sf() as session:
            repo = TenantRepository(session)
            before = await repo.soft_delete_tenant(record_tenant_id)
    except TenantHasActiveBotsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if before is None:
        raise HTTPException(status_code=404, detail="tenant not found")

    await _emit_audit(
        request,
        target_tenant_id=record_tenant_id,
        action="admin_tenant_delete",
        before=_strip_audit_fields(before),
        after=None,
    )

    await _invalidate_cache(request, record_tenant_id)
    # 204 — no body.


__all__ = ["router"]
