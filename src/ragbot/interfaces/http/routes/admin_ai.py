"""Admin AI config routes.

DB-driven model swap — no redeploy needed. Audit logged on every mutation.
Thin controller layer — business logic lives in AIConfigService.

every route gated through the metadata-driven
``module_permissions`` table via :func:`require_permission_dep`.
Role names + numeric levels live in DB, never inlined here.

provider/model mutate routes are seeded at level 100
(super_admin only) because ``ai_providers`` / ``ai_models`` are platform-
shared resources without a ``record_tenant_id`` column. Tenant admins
(level 80) keep read access. Binding mutate routes additionally call
:func:`require_binding_ownership` to pre-verify the row's tenancy before
``AIConfigService`` runs the mutation.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from ragbot.application.services.ai_config_service import (
    BindingBotIdMismatchError,
    BindingNotFoundError,
    ModelDeleteConflictError,
    ModelNotFoundError,
    ProviderNotFoundError,
)
from ragbot.interfaces.http._resource_ownership import require_binding_ownership
from ragbot.interfaces.http.middlewares.rbac import require_permission_dep
from ragbot.interfaces.http.schemas.admin_ai_schemas import (
    AddKeyRequest,
    AdminUpdateBindingRequest,
    AdminUpdateModelRequest,
    AdminUpdateProviderRequest,
    RotateKeyRequest,
)
from ragbot.interfaces.http.schemas.document_schema import (
    AdminCreateBindingRequest,
    AdminCreateModelRequest,
    AdminCreateProviderRequest,
)
from ragbot.shared.errors import KeyNotFoundError, KeyVerifyError
from ragbot.shared.types import BotId, TenantId

router = APIRouter(tags=["admin/ai"])


# Module name for every permission lookup in this file. Keeping the literal
# in one place lets ops rename the module via SQL seed without code edits.
_AI = "ai"


def _svc(request: Request):  # noqa: ANN202
    return request.app.state.container.ai_config_service()


def _audit_tenant_id(request: Request) -> UUID | None:
    tid = getattr(request.state, "record_tenant_id", None)
    if tid is None:
        return None
    return tid if isinstance(tid, UUID) else UUID(str(tid))


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", None) or "admin"


def _trace(request: Request) -> str:
    return getattr(request.state, "trace_id", "n/a")


# --- Providers ---------------------------------------------------------------
@router.get(
    "/ai/providers",
    dependencies=[Depends(require_permission_dep(_AI, "provider_read"))],
)
async def list_providers(request: Request) -> dict[str, object]:
    return {"ok": True, "data": await _svc(request).list_providers()}


@router.post(
    "/ai/providers",
    status_code=201,
    dependencies=[Depends(require_permission_dep(_AI, "provider_create"))],
)
async def create_provider(
    req: AdminCreateProviderRequest, request: Request,
) -> dict[str, object]:
    data = await _svc(request).create_provider(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        actor_user_id=request.state.user_id or "unknown",
        trace_id=request.state.trace_id,
        name=req.name,
        type=req.type,
        base_url=req.base_url,
        auth_type=req.auth_type,
        enabled=req.enabled,
    )
    return {"ok": True, "data": data}


@router.patch(
    "/ai/providers/{provider_id}",
    dependencies=[Depends(require_permission_dep(_AI, "provider_update"))],
)
async def admin_update_provider(
    provider_id: UUID, req: AdminUpdateProviderRequest, request: Request,
) -> dict[str, object]:
    try:
        data = await _svc(request).update_provider(
            provider_id=provider_id,
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
            fields=req.model_dump(exclude_unset=True),
        )
    except ProviderNotFoundError:
        raise HTTPException(404, "provider not found")
    return {"ok": True, "data": data}


@router.delete(
    "/ai/providers/{provider_id}",
    dependencies=[Depends(require_permission_dep(_AI, "provider_delete"))],
)
async def admin_delete_provider(
    provider_id: UUID, request: Request,
) -> dict[str, object]:
    try:
        await _svc(request).delete_provider(
            provider_id=provider_id,
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except ProviderNotFoundError:
        raise HTTPException(404, "provider not found")
    return {"ok": True}


@router.post(
    "/ai/providers/{provider_id}/test",
    dependencies=[Depends(require_permission_dep(_AI, "provider_test"))],
)
async def admin_test_provider(
    provider_id: UUID, request: Request,
) -> dict[str, object]:
    try:
        return await _svc(request).test_provider(provider_id)
    except ProviderNotFoundError:
        raise HTTPException(404, "provider not found")


@router.post(
    "/ai/providers/{provider_id}/rotate-key",
    dependencies=[Depends(require_permission_dep(_AI, "provider_rotate_key"))],
)
async def admin_rotate_key(
    provider_id: UUID, req: RotateKeyRequest, request: Request,
) -> dict[str, object]:
    try:
        await _svc(request).rotate_key(
            provider_id=provider_id,
            plain_key=req.plain_key.get_secret_value(),
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except ProviderNotFoundError:
        raise HTTPException(404, "provider not found")
    return {"ok": True}


# --- Multi-key history per provider ----------------------------------------

@router.post(
    "/ai/providers/{provider_id}/keys",
    status_code=201,
    dependencies=[Depends(require_permission_dep(_AI, "provider_add_key"))],
)
async def admin_add_key(
    provider_id: UUID, req: AddKeyRequest, request: Request,
) -> dict[str, object]:
    """Add new API key to ai_keys, optionally set as default."""
    try:
        result = await _svc(request).add_key(
            provider_id=provider_id,
            plain_key=req.plain_key.get_secret_value(),
            set_as_default=req.set_as_default,
            verify_first=req.verify_first,
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except ProviderNotFoundError:
        raise HTTPException(404, "provider not found")
    except KeyVerifyError as exc:
        raise HTTPException(400, f"key verify failed: {exc}")
    return {"ok": True, "data": result}


@router.get(
    "/ai/providers/{provider_id}/keys",
    dependencies=[Depends(require_permission_dep(_AI, "provider_read"))],
)
async def admin_list_keys(
    provider_id: UUID, request: Request,
) -> dict[str, object]:
    """List all keys masked (fingerprint only — full plain_key never returned)."""
    try:
        keys = await _svc(request).list_keys(provider_id=provider_id)
    except ProviderNotFoundError:
        raise HTTPException(404, "provider not found")
    return {"ok": True, "data": keys}


@router.post(
    "/ai/providers/{provider_id}/keys/{key_id}/verify",
    dependencies=[Depends(require_permission_dep(_AI, "provider_add_key"))],
)
async def admin_verify_key(
    provider_id: UUID, key_id: UUID, request: Request,
) -> dict[str, object]:
    """Re-test an existing key (e.g. check whether balance has been topped up)."""
    try:
        result = await _svc(request).verify_key(
            provider_id=provider_id,
            key_id=key_id,
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except (ProviderNotFoundError, KeyNotFoundError):
        raise HTTPException(404, "provider or key not found")
    return {"ok": True, "data": result}


# --- Models ----------------------------------------------------------------
@router.get(
    "/ai/models",
    dependencies=[Depends(require_permission_dep(_AI, "model_read"))],
)
async def list_models(
    request: Request,
    provider_id: UUID | None = None,
    kind: str | None = None,
) -> dict[str, object]:
    data = await _svc(request).list_models(provider_id=provider_id, kind=kind)
    return {"ok": True, "data": data}


@router.post(
    "/ai/models",
    status_code=201,
    dependencies=[Depends(require_permission_dep(_AI, "model_create"))],
)
async def create_model(
    req: AdminCreateModelRequest, request: Request,
) -> dict[str, object]:
    data = await _svc(request).create_model(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        actor_user_id=request.state.user_id or "unknown",
        trace_id=request.state.trace_id,
        provider_id=req.provider_id,
        name=req.name,
        kind=req.kind,
        context_window=req.context_window,
        max_output_tokens=req.max_output_tokens,
        input_price_per_1k_usd=req.input_price_per_1k_usd,
        output_price_per_1k_usd=req.output_price_per_1k_usd,
        supports_streaming=req.supports_streaming,
        supports_tools=req.supports_tools,
        supports_vision=req.supports_vision,
        supports_json_mode=req.supports_json_mode,
        languages=list(req.languages),
    )
    return {"ok": True, "data": data}


@router.patch(
    "/ai/models/{model_id}",
    dependencies=[Depends(require_permission_dep(_AI, "model_update"))],
)
async def admin_update_model(
    model_id: UUID, req: AdminUpdateModelRequest, request: Request,
) -> dict[str, object]:
    try:
        data = await _svc(request).update_model(
            model_id=model_id,
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
            fields=req.model_dump(exclude_unset=True),
        )
    except ModelNotFoundError:
        raise HTTPException(404, "model not found")
    return {"ok": True, "data": data}


@router.delete(
    "/ai/models/{model_id}",
    dependencies=[Depends(require_permission_dep(_AI, "model_delete"))],
)
async def admin_delete_model(
    model_id: UUID, request: Request,
) -> dict[str, object]:
    try:
        await _svc(request).delete_model(
            model_id=model_id,
            record_tenant_id=_audit_tenant_id(request),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except ModelNotFoundError:
        raise HTTPException(404, "model not found")
    except ModelDeleteConflictError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


# --- Bindings (per-bot per-purpose) ------------------------------------------
@router.get(
    "/bots/{bot_id}/bindings",
    dependencies=[Depends(require_permission_dep(_AI, "binding_read"))],
)
async def list_bindings(bot_id: UUID, request: Request) -> dict[str, object]:
    data = await _svc(request).list_bindings(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        record_bot_id=BotId(bot_id),
    )
    return {"ok": True, "data": data}


@router.post(
    "/bots/{bot_id}/bindings",
    status_code=201,
    dependencies=[Depends(require_permission_dep(_AI, "binding_create"))],
)
async def create_binding(
    bot_id: UUID, req: AdminCreateBindingRequest, request: Request,
) -> dict[str, object]:
    try:
        data = await _svc(request).create_binding(
            record_tenant_id=TenantId(request.state.record_tenant_id),
            record_bot_id=BotId(bot_id),
            actor_user_id=request.state.user_id or "unknown",
            trace_id=request.state.trace_id,
            purpose=req.purpose,
            model_id=req.model_id,
            rank=req.rank,
            variant=req.variant,
            weight=req.weight,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            top_p=req.top_p,
            extra_params=dict(req.extra_params),
            active=req.active,
            request_bot_id=req.bot_id,
        )
    except BindingBotIdMismatchError:
        raise HTTPException(400, "bot_id mismatch")
    return {"ok": True, "data": data}


@router.patch(
    "/bots/{bot_id}/bindings/{binding_id}",
    dependencies=[Depends(require_permission_dep(_AI, "binding_update"))],
)
async def admin_update_binding(
    bot_id: UUID, binding_id: UUID,
    req: AdminUpdateBindingRequest, request: Request,
) -> dict[str, object]:
    # pre-verify binding's record_tenant_id matches
    # caller's tenant (super_admin bypass). 404 on mismatch to prevent
    # cross-tenant enumeration.
    await require_binding_ownership(request, binding_id)
    try:
        data = await _svc(request).update_binding(
            binding_id=binding_id,
            record_tenant_id=TenantId(request.state.record_tenant_id),
            record_bot_id=BotId(bot_id),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
            fields=req.model_dump(exclude_unset=True),
        )
    except BindingNotFoundError:
        raise HTTPException(404, "binding not found")
    return {"ok": True, "data": data}


@router.delete(
    "/bots/{bot_id}/bindings/{binding_id}",
    dependencies=[Depends(require_permission_dep(_AI, "binding_delete"))],
)
async def admin_delete_binding(
    bot_id: UUID, binding_id: UUID, request: Request,
) -> dict[str, object]:
    # pre-verify binding's record_tenant_id matches
    # caller's tenant (super_admin bypass).
    await require_binding_ownership(request, binding_id)
    try:
        await _svc(request).delete_binding(
            binding_id=binding_id,
            record_tenant_id=TenantId(request.state.record_tenant_id),
            record_bot_id=BotId(bot_id),
            actor_user_id=_actor(request),
            trace_id=_trace(request),
        )
    except BindingNotFoundError:
        raise HTTPException(404, "binding not found")
    return {"ok": True}


@router.get(
    "/bots/{bot_id}/audit-log",
    dependencies=[Depends(require_permission_dep(_AI, "audit_read"))],
)
async def list_audit(
    bot_id: UUID, request: Request, limit: int = 100,
) -> dict[str, object]:
    data = await _svc(request).list_audit(
        record_tenant_id=TenantId(request.state.record_tenant_id),
        record_bot_id=BotId(bot_id),
        limit=limit,
    )
    return {"ok": True, "data": data}


# --- Cache admin --------------------------------------------------------------
@router.post(
    "/ai/cache/reload",
    dependencies=[Depends(require_permission_dep(_AI, "cache_reload"))],
)
async def admin_cache_reload(request: Request) -> dict[str, object]:
    count = await _svc(request).cache_reload()
    return {"ok": True, "entries_loaded": count}


@router.get(
    "/ai/cache/status",
    dependencies=[Depends(require_permission_dep(_AI, "cache_status"))],
)
async def admin_cache_status(request: Request) -> dict[str, object]:
    status = await _svc(request).cache_status()
    return {"ok": True, "data": status}


# --- Effective config preview -------------------------------------------------
@router.get(
    "/ai/models/{model_id}/effective-config",
    dependencies=[Depends(require_permission_dep(_AI, "effective_config_read"))],
)
async def admin_effective_config(
    model_id: UUID, request: Request, bot_id: UUID | None = None,
) -> dict[str, object]:
    data = await _svc(request).effective_config(model_id=model_id, bot_id=bot_id)
    return {"ok": True, "data": data}


__all__ = ["router"]
