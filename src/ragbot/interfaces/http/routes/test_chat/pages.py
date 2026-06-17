"""UI HTML pages + DEV self-token endpoint for the test_chat package.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). These
routes register on ``pages_router`` (NOT the api ``router``); ``__init__``
aggregates this module's ``pages_router`` into the package ``pages_router``.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from ragbot.shared.constants import (
    DEFAULT_DEV_TOKEN_ALLOW_NETWORK,
    DEFAULT_DEV_TOKEN_ENABLED,
    DEFAULT_DEV_TOKEN_SELF_TTL_REDIS_S,
    FALLBACK_RATE_LIMIT_WINDOW,
)

from ._shared import (
    _STATIC_DIR,
    _container,
    _sf,
    _sys_config,
    _token_service,
    logger,
)

pages_router = APIRouter(tags=["pages"], include_in_schema=False)


@pages_router.get("/demo-ragbot", response_class=HTMLResponse)
async def bot_list_page() -> HTMLResponse:
    """Hiển thị trang danh sách bot demo.
    @return: HTML trang test-bots
    """
    p = _STATIC_DIR / "test-bots.html"
    if p.exists():
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>test-bots.html not found</h1>", status_code=404)


@pages_router.get("/demo-ragbot/reupload", response_class=HTMLResponse)
async def reupload_page() -> HTMLResponse:
    """One-click re-upload screen for the 3 demo bots (9 fixed URLs).

    Reads the fixed seed config server-side via ``/api/ragbot/test/reinit-bots``;
    the page is pure presentation. Single source of truth = bot_sources.json.
    """
    p = _STATIC_DIR / "reupload.html"
    if p.exists():
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>reupload.html not found</h1>", status_code=404)


@pages_router.get("/demo-ragbot/bot/{bot_id}/{channel_type}", response_class=HTMLResponse)
async def bot_detail_page(bot_id: str, channel_type: str) -> HTMLResponse:
    """Hiển thị trang chi tiết bot theo bot_id và channel_type.
    @param bot_id, channel_type: định danh bot
    @return: HTML trang test-bot-detail
    """
    p = _STATIC_DIR / "test-bot-detail.html"
    if p.exists():
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>test-bot-detail.html not found</h1>", status_code=404)


@pages_router.get("/demo-ragbot/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """Trang admin — quản lý system config, Redis, tokens, models.
    @return: HTMLResponse
    """
    p = _STATIC_DIR / "admin.html"
    if p.exists():
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>admin.html not found</h1>", status_code=404)


@pages_router.get("/test-chat.html", response_class=HTMLResponse)
async def test_chat_page_classic() -> HTMLResponse:
    """Redirect trang test-chat cũ sang /demo-ragbot.
    @return: HTML redirect
    """
    return HTMLResponse('<html><head><meta http-equiv="refresh" content="0;url=/demo-ragbot"></head></html>')


@pages_router.get("/ragbot", response_class=HTMLResponse)
async def ragbot_redirect() -> HTMLResponse:
    """Redirect /ragbot sang /demo-ragbot.
    @return: HTML redirect
    """
    return HTMLResponse('<html><head><meta http-equiv="refresh" content="0;url=/demo-ragbot"></head></html>')


def _bool_env(name: str, default: bool) -> bool:
    """Parse a boolean env var; returns ``default`` if unset/blank."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


@pages_router.get("/api/ragbot/test/tokens/self")
async def get_self_token(request: Request) -> dict:
    """Lấy hoặc tạo token self-service cho ragbot frontend (DEV-ONLY).
    @return: {ok, token}
    @raises: HTTPException 404 if RAGBOT_DEV_TOKEN_ENABLED is not true
    @raises: HTTPException 403 if caller is not loopback and
        RAGBOT_DEV_TOKEN_ALLOW_NETWORK is not true
    """
    # Hard gate 1 — feature flag (default OFF in production).
    if not _bool_env("RAGBOT_DEV_TOKEN_ENABLED", DEFAULT_DEV_TOKEN_ENABLED):
        raise HTTPException(status_code=404, detail="not found")

    # Hard gate 2 — loopback-only unless explicitly opened on a private LAN.
    client_host = (request.client.host if request.client else "")
    allow_network = _bool_env(
        "RAGBOT_DEV_TOKEN_ALLOW_NETWORK", DEFAULT_DEV_TOKEN_ALLOW_NETWORK,
    )
    if not allow_network and client_host not in _LOOPBACK_HOSTS:
        logger.warning(
            "dev_token_endpoint_blocked_non_loopback",
            client_host=client_host,
        )
        raise HTTPException(
            status_code=403, detail="dev token endpoint locked to localhost",
        )

    svc = await _token_service(request)
    redis = _container(request).redis_client()
    sf = _sf(request)

    async with sf() as session:
        row = (await session.execute(
            text("SELECT version FROM api_tokens WHERE service_name = 'ragbot-self' AND revoked_at IS NULL"),
        )).fetchone()

    # Try Redis cache first — avoid regenerating on every page load
    _self_token_key = "ragbot:self_token"
    cached_token = await redis.get(_self_token_key)
    if cached_token:
        return {"ok": True, "token": cached_token.decode() if isinstance(cached_token, bytes) else cached_token}

    # Dev tenant scope — RAGBOT_DEV_TOKEN_TENANT_ID accepts either a UUID
    # (preferred) or a legacy upstream INT (resolved via tenants.config);
    # the JWT carries record_tenant_id UUID either way.
    _dev_tid_raw = os.environ.get("RAGBOT_DEV_TOKEN_TENANT_ID")
    _dev_record_tenant: uuid.UUID | None = None
    if _dev_tid_raw:
        try:
            _dev_record_tenant = uuid.UUID(_dev_tid_raw)
        except ValueError:
            try:
                upstream = int(_dev_tid_raw)
                async with sf() as session:
                    row_t = (await session.execute(
                        text(
                            "SELECT id FROM tenants "
                            "WHERE (config->>'upstream_tenant_id')::int = :tid LIMIT 1"
                        ),
                        {"tid": upstream},
                    )).fetchone()
                    if row_t:
                        _dev_record_tenant = uuid.UUID(str(row_t[0]))
            except (ValueError, TypeError):
                logger.warning("dev_token_tenant_id_invalid", raw=_dev_tid_raw)

    if row is None:
        result = await svc.create_token(
            "ragbot-self", "Auto-generated self-service token",
            redis_client=redis, role="owner", rate_limit_value=0,
            rate_limit_window=FALLBACK_RATE_LIMIT_WINDOW,
            record_tenant_id=_dev_record_tenant,
        )
        token = result["token"]
    else:
        result = await svc.regenerate_token(
            "ragbot-self", redis_client=redis, record_tenant_id=_dev_record_tenant,
        )
        token = result["token"]

    # Cache raw token for 5 min — all tabs share same token, no version bump
    await redis.set(_self_token_key, token, ex=DEFAULT_DEV_TOKEN_SELF_TTL_REDIS_S)
    return {"ok": True, "token": token}


__all__ = ["pages_router", "get_self_token"]
