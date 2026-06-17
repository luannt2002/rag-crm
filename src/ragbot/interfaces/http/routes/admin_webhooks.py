"""Admin endpoints for tenant webhook secret rotation.

POST /admin/webhooks/{webhook_id}/rotate-secret
-----------------------------------------------
Mint a new HMAC secret for the webhook identified by ``webhook_id``.
The previous active secret is revoked with the configured grace period
so partner integrations can roll their consumer without downtime.

The plain secret is returned EXACTLY ONCE in the response body. The DB
only stores its scrypt hash — re-derivation is impossible. The caller
MUST capture the secret immediately; subsequent ``rotate`` calls
generate fresh secrets and never replay the old one.

Authorisation
-------------
* RBAC: ``require_min_level(80)`` — tenant admin only. Platform
  super-admin (level 100) inherits.
* Tenant isolation: the route lifts ``record_tenant_id`` from the JWT
  bearer (``request.state.record_tenant_id``) and refuses to operate
  on a webhook owned by a different tenant. Super-admin bypasses the
  filter (so platform ops can rotate on a tenant's behalf during an
  incident).

Audit
-----
Every rotation emits one ``audit_log`` row via the shared
``insert_audit_row`` writer (the chained, tamper-evident table).
The before/after JSON captures the version numbers, not the secret.

GET /admin/webhooks/{webhook_id}/secret-versions
------------------------------------------------
Returns version metadata (``version``, ``created_at``, ``revoked_at``,
``grace_period_hours``) — never the hash. Useful for audit trail UI.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.services.webhook_secret_rotation import (
    WebhookSecretRotationService,
)
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.shared.constants import (
    DEFAULT_SUPER_ADMIN_LEVEL,
    DEFAULT_TENANT_ADMIN_LEVEL,
    WORKSPACE_SYSTEM_SLUG,
)
from ragbot.shared.rbac import check_min_level, require_min_level

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin/webhooks"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _require_admin(request: Request) -> None:
    """Gate — tenant-admin level (80) or higher."""
    require_min_level(request, DEFAULT_TENANT_ADMIN_LEVEL)


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", None) or "admin"


def _trace(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def _caller_record_tenant(request: Request) -> UUID | None:
    """Caller's tenant UUID, or ``None`` when the token is platform-admin."""
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _resolve_webhook_owner(
    session, webhook_id: UUID,
) -> UUID | None:
    """Look up the tenant that owns ``webhook_id`` (or None when not found).

    Used to enforce cross-tenant isolation BEFORE the rotation runs: a
    caller from tenant B trying to rotate tenant A's webhook hits 403
    here, never touches the secrets table.
    """
    result = await session.execute(
        text(
            "SELECT record_tenant_id FROM tenant_webhooks "
            "WHERE id = :wid AND revoked_at IS NULL",
        ),
        {"wid": webhook_id},
    )
    row = result.first()
    return row.record_tenant_id if row else None


# ── Routes ─────────────────────────────────────────────────────────────────


@router.post("/webhooks/{webhook_id}/rotate-secret", status_code=201)
async def admin_rotate_webhook_secret(
    webhook_id: UUID, request: Request,
) -> dict[str, Any]:
    """Mint a new HMAC secret + revoke the previous (grace-period preserved)."""
    _require_admin(request)

    caller_tenant = _caller_record_tenant(request)
    is_super = check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL)

    container = request.app.state.container
    sf = container.session_factory()

    try:
        async with sf() as session:
            owner_tenant = await _resolve_webhook_owner(session, webhook_id)
            if owner_tenant is None:
                raise HTTPException(status_code=404, detail="webhook not found")

            # Cross-tenant guard: non-super callers can only rotate
            # webhooks under their OWN tenant. Super-admin (level 100)
            # bypasses for ops incidents.
            if not is_super:
                if caller_tenant is None or owner_tenant != caller_tenant:
                    raise HTTPException(
                        status_code=403, detail="cross-tenant forbidden",
                    )

            service = WebhookSecretRotationService(session)
            result = await service.rotate(
                record_tenant_id=owner_tenant, webhook_id=webhook_id,
            )

            # Audit BEFORE commit — same transaction so we never log a
            # rotation that did not actually land.
            previous = result["version"] - 1 if result["version"] > 1 else None
            await insert_audit_row(
                session,
                record_tenant_id=owner_tenant,
                workspace_id=WORKSPACE_SYSTEM_SLUG,
                actor_user_id=_actor(request),
                action="webhook_secret_rotate",
                resource_type="tenant_webhook",
                resource_id=str(webhook_id),
                before_json=(
                    {"version": previous} if previous else None
                ),
                after_json={"version": result["version"]},
                trace_id=_trace(request),
            )
            await session.commit()
    except HTTPException:
        raise
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        logger.exception(
            "admin_webhook_secret_rotate_failed",
            webhook_id=str(webhook_id),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=500, detail="webhook secret rotation failed",
        ) from exc

    return {
        "ok": True,
        "data": {
            "version": result["version"],
            "secret": result["secret"],
            "created_at": result["created_at"].isoformat(),
            # Reminder for client integrations — the plain secret will
            # not appear in any subsequent GET / list call.
            "warning": (
                "Capture this secret now — it is shown exactly once."
            ),
        },
    }


@router.get("/webhooks/{webhook_id}/secret-versions")
async def admin_list_webhook_secret_versions(
    webhook_id: UUID, request: Request,
) -> dict[str, Any]:
    """List historic versions (hash never returned)."""
    _require_admin(request)

    caller_tenant = _caller_record_tenant(request)
    is_super = check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL)

    container = request.app.state.container
    sf = container.session_factory()

    async with sf() as session:
        owner_tenant = await _resolve_webhook_owner(session, webhook_id)
        if owner_tenant is None:
            raise HTTPException(status_code=404, detail="webhook not found")
        if not is_super:
            if caller_tenant is None or owner_tenant != caller_tenant:
                raise HTTPException(
                    status_code=403, detail="cross-tenant forbidden",
                )

        service = WebhookSecretRotationService(session)
        versions = await service.list_versions(
            record_tenant_id=owner_tenant, webhook_id=webhook_id,
        )

    return {
        "ok": True,
        "data": [
            {
                "version": row["version"],
                "created_at": row["created_at"].isoformat(),
                "revoked_at": (
                    row["revoked_at"].isoformat() if row["revoked_at"] else None
                ),
                "grace_period_hours": row["grace_period_hours"],
            }
            for row in versions
        ],
    }


__all__ = ["router"]
