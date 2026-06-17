"""Admin CRUD bots — thin controller delegating to BotManagementService.

Mutations also publish `bot.registry.changed.v1` via the outbox so peer
replicas can bust their local registry cache. Non-superadmin callers are
constrained to their own ``record_tenant_id``; superadmin bypasses the filter.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ragbot.application.services.bot_lifecycle_service import (
    BotNotPurgeableError,
)
from ragbot.application.services.bot_management_service import (
    BotNotFoundError,
    CreateBotCommand,
    CrossTenantForbiddenError,
    UpdateBotCommand,
)
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.shared.callback_validator import validate_callback_url
from ragbot.shared.constants import DEFAULT_SUPER_ADMIN_LEVEL, DEFAULT_TENANT_ADMIN_LEVEL
from ragbot.shared.rbac import check_min_level, require_min_level

router = APIRouter(prefix="/bots", tags=["admin/bots"])


# ── Request-scoped helpers ──────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    require_min_level(request, DEFAULT_TENANT_ADMIN_LEVEL)


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", None) or "admin"


def _trace(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def _admin_record_tenant(request: Request) -> UUID | None:
    """Tenant UUID for row-scoping. ``None`` => platform admin bypass."""
    if check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL):
        return None  # platform admin sees all
    return getattr(request.state, "record_tenant_id", None)


def _bot_svc(request: Request):  # noqa: ANN202
    return request.app.state.container.bot_management_service()


# ── Routes ──────────────────────────────────────────────────────────────────

@router.post(
    "",
    dependencies=[Depends(require_permission_dep("bot", "create"))],
)
async def admin_create_bot(req: CreateBotCommand, request: Request) -> dict:
    if req.callback_url:
        ok, msg = await validate_callback_url(req.callback_url)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Callback URL validation failed: {msg}")
    try:
        cfg = await _bot_svc(request).create_bot(
            req,
            admin_record_tenant=_admin_record_tenant(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except CrossTenantForbiddenError:
        raise HTTPException(status_code=403, detail="cross-tenant forbidden")
    return {"ok": True, "data": cfg.model_dump(mode="json")}


@router.patch(
    "/{bot_uuid}",
    dependencies=[Depends(require_permission_dep("bot", "update"))],
)
async def admin_update_bot(
    bot_uuid: UUID, req: UpdateBotCommand, request: Request,
) -> dict:
    if req.callback_url:
        ok, msg = await validate_callback_url(req.callback_url)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Callback URL validation failed: {msg}")
    try:
        updated = await _bot_svc(request).update_bot(
            bot_uuid,
            req,
            admin_record_tenant=_admin_record_tenant(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except BotNotFoundError:
        raise HTTPException(status_code=404, detail="bot not found")
    except CrossTenantForbiddenError:
        raise HTTPException(status_code=403, detail="cross-tenant forbidden")
    return {"ok": True, "data": updated.model_dump(mode="json")}


@router.delete(
    "/{bot_uuid}",
    dependencies=[Depends(require_permission_dep("bot", "delete"))],
)
async def admin_delete_bot(bot_uuid: UUID, request: Request) -> dict:
    try:
        ok = await _bot_svc(request).delete_bot(
            bot_uuid,
            admin_record_tenant=_admin_record_tenant(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except BotNotFoundError:
        raise HTTPException(status_code=404, detail="bot not found")
    return {"ok": True, "deleted": ok}


@router.post(
    "/{bot_uuid}/purge",
    dependencies=[Depends(require_permission_dep("bot", "delete"))],
)
async def admin_purge_bot(bot_uuid: UUID, request: Request) -> dict:
    """Irreversible purge — phase 2 after the soft delete (grace window).

    Hard-deletes the bots row (FK CASCADE wipes chunks / semantic_cache /
    conversations / ...) and busts registry + corpus_version + uq caches.
    The tenant claim is REQUIRED even for platform admin: with RLS active
    a tenant-less DELETE silently matches zero rows (ADR-W1-D4 R2).
    """
    record_tenant_id = getattr(request.state, "record_tenant_id", None)
    if record_tenant_id is None:
        raise HTTPException(
            status_code=422,
            detail="record_tenant_id claim required for purge",
        )
    svc = request.app.state.container.bot_lifecycle_service()
    try:
        report = await svc.purge_bot(
            bot_uuid,
            record_tenant_id=record_tenant_id,
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except BotNotPurgeableError:
        raise HTTPException(
            status_code=409,
            detail="bot is not soft-deleted — delete it first (grace window)",
        )
    if not report.purged:
        raise HTTPException(status_code=404, detail="bot not found")
    return {"ok": True, "data": report.model_dump(mode="json")}


@router.get(
    "",
    dependencies=[Depends(require_permission_dep("bot", "list"))],
)
async def admin_list_bots(
    request: Request,
    record_tenant_id: UUID | None = Query(
        None,
        description=(
            "Optional filter for super-admin (RBAC level 100) to "
            "narrow the cross-tenant listing. Tenant admins are "
            "always constrained to their JWT tenant — this query "
            "value is ignored for non-super-admin callers."
        ),
    ),
    channel_type: str | None = Query(None, description="Optional channel filter"),
) -> dict:
    """List active bots, scoped by JWT tenant (or platform-wide for super-admin).

    ``record_tenant_id`` here is a JUSTIFIED OPTIONAL **query filter**, NOT
    the 3-key identity. The identity comes from the JWT via
    ``_admin_record_tenant(request)``; a tenant admin's listing is locked to
    its own tenant regardless of the query value. The query exists so a
    super-admin (level 100) can narrow cross-tenant listing without hitting
    another endpoint.
    """
    bots = await _bot_svc(request).list_bots(
        admin_record_tenant=_admin_record_tenant(request),
        record_tenant_id=record_tenant_id,
        channel_type=channel_type,
    )
    return {"ok": True, "data": [b.model_dump(mode="json") for b in bots]}


def _effective_prompt_payload(bot, *, effective: str) -> dict:
    """Split the assembled prompt into owner-base vs platform-appended.

    The assembler guarantees owner content is the exact prefix (pinned by
    ``test_sysprompt_assembler_pin.py``), so the diff is a plain suffix cut.
    """
    base = getattr(bot, "system_prompt", None) or ""
    appended = effective[len(base):] if effective.startswith(base) else ""
    plan_limits = getattr(bot, "plan_limits", None) or {}
    disabled = plan_limits.get("sysprompt_rules_disabled", []) or []
    return {
        "base_prompt": base,
        "platform_appended": appended,
        "effective_prompt": effective,
        "disabled_rule_ids": list(disabled),
    }


@router.get(
    "/{bot_uuid}/effective-prompt",
    dependencies=[Depends(require_permission_dep("bot", "list"))],
)
async def admin_bot_effective_prompt(bot_uuid: UUID, request: Request) -> dict:
    """Read-only transparency view: the FINAL system prompt the LLM sees.

    Owner content + platform-default rules (post opt-out). Required by
    ADR-W1-S10 condition 1 — the platform-rule append is only permitted
    while the owner can inspect exactly what got appended.
    """
    _require_admin(request)
    try:
        cfg = await _bot_svc(request).get_bot(
            bot_uuid, admin_record_tenant=_admin_record_tenant(request),
        )
    except BotNotFoundError:
        raise HTTPException(status_code=404, detail="bot not found")

    assembler = request.app.state.container.sysprompt_assembler()
    effective = await assembler.assemble(
        bot=cfg, language=getattr(cfg, "language", None),
    )
    return {"ok": True, "data": _effective_prompt_payload(cfg, effective=effective)}


@router.get(
    "/cache/status",
    dependencies=[Depends(require_permission_dep("bot", "cache_status"))],
)
async def admin_bot_cache_status(request: Request) -> dict:
    _require_admin(request)
    registry = request.app.state.container.bot_registry_service()
    return {"ok": True, "data": await registry.cache_status()}


@router.post(
    "/cache/reload",
    dependencies=[Depends(require_permission_dep("bot", "cache_reload"))],
)
async def admin_bot_cache_reload(request: Request) -> dict:
    _require_admin(request)
    registry = request.app.state.container.bot_registry_service()
    count = await registry.bootstrap_cache()
    return {"ok": True, "loaded": count}


__all__ = ["router"]
