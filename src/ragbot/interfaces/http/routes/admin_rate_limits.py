"""Partner-facing rate-limit inspection endpoint (2026-05-16).

Multi-tenant SaaS contract: partners (BE-to-BE integrators, customer
admins) need a way to read **both** the policy that applies to their
bots and their **current consumption** so they can rate-limit on their
side before our limiter 429s their traffic.

Endpoint
--------
``GET /api/ragbot/admin/rate-limits/inspect``

Lists, for each bot the caller owns:

* The 4-key tuple ``(record_tenant_id, workspace_id, bot_id, channel_type)``
* Per-policy caps (chat, ingest) with window seconds + per-window limit
* Current consumption in the active window (Redis counter read-only)
* Seconds until the bucket resets

RBAC: requires level 60 (admin) on caller JWT. Tenant scope is bound to
the caller's own ``record_tenant_id`` so a tenant admin cannot probe
another tenant's bots.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import text as _sql_text

from ragbot.shared.constants import (
    DEFAULT_ADMIN_LEVEL,
    DEFAULT_RL_BOT_PER_MIN,
    DEFAULT_RL_UPLOAD_PER_MIN,
    DEFAULT_RL_WINDOW_S,
)
from ragbot.shared.rbac import require_min_level

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin/rate-limits"])

_BOT_RL_PREFIX = "ragbot:rl:bot:"


def _require_admin_tenant(request: Request) -> UUID:
    require_min_level(request, DEFAULT_ADMIN_LEVEL)
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=401, detail="missing tenant context")
    return record_tenant if isinstance(record_tenant, UUID) else UUID(str(record_tenant))


def _redis_client(request: Request) -> Any | None:
    container = getattr(request.app.state, "container", None)
    if container is None:
        return None
    provider = getattr(container, "redis_client", None)
    if provider is None:
        return None
    try:
        return provider()
    except (KeyError, TypeError, RuntimeError):
        return None


async def _read_bucket(
    redis_client: Any,
    *,
    record_tenant_id: str,
    workspace_id: str,
    bot_id: str,
    channel_type: str,
    window_s: int,
) -> int:
    """Return the current count in the active fixed-window bucket.

    A read-only ``GET`` against the Redis key — no INCR. Missing key
    (no requests yet this window) returns 0.
    """
    bucket = int(time.time() // window_s)
    key = (
        f"{_BOT_RL_PREFIX}{record_tenant_id}:{workspace_id}:{bot_id}:"
        f"{channel_type}:{bucket}"
    )
    try:
        raw = await redis_client.get(key)
    except (RedisError, OSError, asyncio.TimeoutError):
        # Narrow degrade-open: diagnostic endpoint, partner only reads
        # consumption — Redis blip returns 0 rather than 5xx.
        return 0
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


@router.get("/admin/rate-limits/inspect")
async def inspect_rate_limits(request: Request) -> dict[str, Any]:
    """List policies + current consumption for the caller-tenant's bots.

    The response is the **public partner contract**: partners scrape
    this on a polling cadence (1 req/min recommended) and apply
    client-side back-pressure before hitting our 429.
    """
    record_tenant = _require_admin_tenant(request)
    redis_client = _redis_client(request)
    container = request.app.state.container
    sf = container.session_factory()

    async with sf() as session:
        rows = await session.execute(
            _sql_text(
                """
                SELECT b.bot_id, b.channel_type, b.workspace_id
                FROM bots b
                WHERE b.record_tenant_id = :tenant_id
                  AND b.is_deleted = false
                ORDER BY b.workspace_id, b.bot_id, b.channel_type
                """,
            ),
            {"tenant_id": record_tenant},
        )
        bots = rows.fetchall()

    window_s = DEFAULT_RL_WINDOW_S
    chat_limit = DEFAULT_RL_BOT_PER_MIN
    ingest_limit = DEFAULT_RL_UPLOAD_PER_MIN
    reset_s = window_s - int(time.time() % window_s)

    items: list[dict[str, Any]] = []
    for row in bots:
        bot_id, channel_type, workspace_id = row[0], row[1], row[2]
        consumption = 0
        if redis_client is not None:
            consumption = await _read_bucket(
                redis_client,
                record_tenant_id=str(record_tenant),
                workspace_id=str(workspace_id),
                bot_id=str(bot_id),
                channel_type=str(channel_type),
                window_s=window_s,
            )
        items.append(
            {
                "identity": {
                    "record_tenant_id": str(record_tenant),
                    "workspace_id": str(workspace_id),
                    "bot_id": str(bot_id),
                    "channel_type": str(channel_type),
                },
                "policies": {
                    "chat": {
                        "per_window": chat_limit,
                        "window_s": window_s,
                        "current_count": consumption,
                        "remaining": max(0, chat_limit - consumption),
                        "reset_in_s": reset_s,
                    },
                    "ingest": {
                        "per_window": ingest_limit,
                        "window_s": window_s,
                        "current_count": consumption,
                        "remaining": max(0, ingest_limit - consumption),
                        "reset_in_s": reset_s,
                    },
                },
            },
        )

    return {
        "ok": True,
        "data": {
            "record_tenant_id": str(record_tenant),
            "policy_version": 1,
            "window_s": window_s,
            "bots": items,
            "notes": [
                "current_count = combined chat + ingest counter on the same "
                "4-key bucket; chat / ingest share window but ingest has a "
                "tighter cap (request kicks a worker pipeline).",
                "remaining is clamped to >= 0; receiving a 429 BOT_RATE_LIMIT_EXCEEDED "
                "is expected when remaining hits 0 before reset_in_s.",
                "Partner SLA: poll this endpoint at ≤ 1 req/min for headroom; do not "
                "use it as a real-time throttle replacement.",
            ],
        },
    }


__all__ = ["router"]
