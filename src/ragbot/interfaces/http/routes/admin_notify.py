"""Admin notify-channel routes — manage webhook target without redeploy.

Endpoints (all under ``/api/ragbot/admin``):

* ``GET    /admin/notify-channel``       — read masked config + source.
* ``PATCH  /admin/notify-channel``       — upsert ``system_config`` row.
* ``DELETE /admin/notify-channel``       — drop row → fall back to env.
* ``POST   /admin/notify-channel/test``  — fire a synthetic dispatch.

Every mutation invalidates the resolver's Redis cache so the next
dispatch picks up the change on the very next call. RBAC gate is the
shared ``DEFAULT_ADMIN_LEVEL`` (60+); platform admins (level 100)
implicitly satisfy it.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

from ragbot.application.dto.notify_channel import NotifyChannelConfig
from ragbot.shared.constants import (
    DEFAULT_ADMIN_LEVEL,
    NOTIFY_CHANNEL_CONFIG_KEY,
)
from ragbot.shared.rbac import require_min_level

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin/notify"])


def _require_admin(request: Request) -> None:
    """Enforce admin-tier (level 60) RBAC on every notify route."""
    require_min_level(request, DEFAULT_ADMIN_LEVEL)


def _container(request: Request):
    return request.app.state.container


@router.get("/notify-channel")
async def admin_get_notify_channel(request: Request) -> dict[str, Any]:
    """Return the current config (webhook_key masked) + resolution source."""
    _require_admin(request)

    resolver = _container(request).notify_resolver()
    cfg, source = await resolver.resolve()
    if cfg is None:
        return {"ok": True, "source": source, "config": None}
    return {"ok": True, "source": source, "config": cfg.mask_for_log()}


@router.patch("/notify-channel")
async def admin_patch_notify_channel(
    payload: NotifyChannelConfig,
    request: Request,
) -> dict[str, Any]:
    """Upsert ``system_config`` row; invalidate resolver cache."""
    _require_admin(request)

    container = _container(request)
    scs = container.system_config_service()
    resolver = container.notify_resolver()

    # Persist the full DTO as a JSON dict; ``model_dump(mode="json")``
    # serialises ``HttpUrl`` to a string so JSONB stores cleanly.
    await scs.set(
        NOTIFY_CHANNEL_CONFIG_KEY,
        payload.model_dump(mode="json"),
        description="Webhook target for error alerts",
    )
    await resolver.invalidate()

    logger.info(
        "admin_notify_channel_updated",
        actor=getattr(request.state, "user_id", None) or "unknown",
        config=payload.mask_for_log(),
    )
    return {"ok": True, "source": "db", "config": payload.mask_for_log()}


@router.delete("/notify-channel")
async def admin_delete_notify_channel(request: Request) -> dict[str, Any]:
    """Clear the DB row — resolver falls back to the env config."""
    _require_admin(request)

    container = _container(request)
    scs = container.system_config_service()
    resolver = container.notify_resolver()

    # Use ``set`` with ``None`` then explicitly drop the row would be
    # cleanest, but the existing service surface only exposes ``set``.
    # Calling ``set(..., None)`` keeps the row but stores JSON null;
    # the resolver treats non-dict values as "no DB config" and falls
    # through to env, which matches the documented behaviour.
    await scs.set(NOTIFY_CHANNEL_CONFIG_KEY, None)
    await resolver.invalidate()

    logger.info(
        "admin_notify_channel_cleared",
        actor=getattr(request.state, "user_id", None) or "unknown",
    )
    return {"ok": True, "source_after_delete": "env_or_none"}


@router.post("/notify-channel/test")
async def admin_test_notify_channel(
    request: Request,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fire a synthetic ``info`` dispatch through the live channel."""
    _require_admin(request)

    container = _container(request)
    dispatcher = container.webhook_dispatcher()

    body = payload or {}
    message = str(body.get("message") or "manual test from admin")

    outcome = await dispatcher.dispatch(
        severity="info",
        component="admin_test",
        message=message,
        error_type="AdminTest",
    )
    if not outcome.get("dispatched") and outcome.get("reason") == "unconfigured":
        raise HTTPException(
            status_code=409,
            detail="notify channel not configured (no DB row, no env fallback)",
        )
    return {"ok": True, **outcome}


__all__ = ["router"]
