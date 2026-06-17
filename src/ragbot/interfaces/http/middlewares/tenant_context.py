"""Tenant authentication middleware: service JWT (HS256) then user JWT (RS256), with layered rate limits.

Tenant identity is carried by JWT claim ``record_tenant_id`` (UUID, FK to
``tenants.id``). Legacy ``tenant_id`` int claim (for transitional tokens)
is accepted by resolving via ``tenants.config->>'upstream_tenant_id'``
and binding the resolved UUID to ``request.state.record_tenant_id``.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import text as sa_text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ragbot.application.services.jwt_token_service import JwtTokenService
from ragbot.config.logging import bind_request_context
from ragbot.infrastructure.observability.metrics import (
    rate_limit_backend_error_total,
    rate_limit_fail_closed_total,
)
from ragbot.shared.constants import (
    DEFAULT_HONEYPOT_PATHS,
    DEFAULT_RL_FAIL_CLOSED_RETRY_S,
    DEFAULT_SUPER_ADMIN_LEVEL,
)
from ragbot.shared.rbac import check_min_level

logger = structlog.get_logger(__name__)

_SYSTEM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
_RL_PREFIX = "ragbot:rl:"


class RateLimitBackendUnavailable(Exception):
    """Raised on Redis error during rate-limit check."""


_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/health/models",
        "/metrics",
        # Browsers auto-fetch /favicon.ico on every page; serving it as
        # public avoids 401 floods that the anti-abuse middleware counts
        # as auth failures (10 in a minute → IP ban).
        "/favicon.ico",
        # Honeypot routes are public-by-design so the route handler
        # (which logs + flags the source IP) actually runs. Skipping auth here
        # preserves the 404 disguise: the scanner cannot infer the path is a
        # trap from a 401 vs 404 difference.
        *DEFAULT_HONEYPOT_PATHS,
    },
)


async def _resolve_upstream_int_tenant(
    session_factory: Any,
    upstream_int: int,
) -> UUID | None:
    """Resolve upstream INT tenant_id → record_tenant_id UUID via tenants.config."""
    try:
        async with session_factory() as session:
            row = await session.execute(
                sa_text(
                    "SELECT id FROM tenants "
                    "WHERE (config->>'upstream_tenant_id')::int = :tid LIMIT 1"
                ),
                {"tid": int(upstream_int)},
            )
            scalar = row.scalar()
            return UUID(str(scalar)) if scalar else None
    except Exception as exc:  # noqa: BLE001 — best-effort resolve
        logger.debug("upstream_int_tenant_resolve_failed", err=str(exc), upstream=upstream_int)
        return None


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Authenticates Bearer tokens and binds record_tenant_id / user_id / role on request.state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if (
            path in _PUBLIC_PATHS
            or path.startswith("/demo-ragbot")
            or path.startswith("/static/")
            or path == "/api/ragbot/test/tokens/self"
        ):
            if not hasattr(request.state, "record_tenant_id"):
                request.state.record_tenant_id = None
                request.state.user_id = None
                request.state.bot_id = None
                request.state.role = "system"
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

        if not token:
            return _unauth("missing bearer token", request)

        # Service JWT path (HS256).
        _service_jwt_ok = False
        try:
            container = request.app.state.container
            settings = request.app.state.settings
            svc = JwtTokenService(
                session_factory=container.session_factory(),
                jwt_secret=settings.app.api_token,
            )
            redis = container.redis_client()
            payload = await svc.verify_token(token, redis_client=redis)
            if payload is not None:
                service_name = payload.get("sub", "system")
                role = payload.get("role", "service")
                rl_val = payload.get("rl_val", 120)
                rl_win = payload.get("rl_win", 60)

                # Resolve record_tenant_id UUID — prefer the explicit UUID claim,
                # fall back to upstream INT (via tenants.config lookup) for
                # rolling-upgrade compat with older mintings.
                record_tenant_id: UUID | None = None
                rt_claim = payload.get("record_tenant_id")
                if rt_claim:
                    try:
                        record_tenant_id = UUID(str(rt_claim))
                    except (TypeError, ValueError):
                        record_tenant_id = None
                if record_tenant_id is None and payload.get("tenant_id") is not None:
                    try:
                        record_tenant_id = await _resolve_upstream_int_tenant(
                            container.session_factory(), int(payload["tenant_id"]),
                        )
                    except (TypeError, ValueError):
                        record_tenant_id = None

                if record_tenant_id is None and role not in ("owner", "super_admin"):
                    logger.warning(
                        "service_jwt_missing_tenant_claim_rejected",
                        service_name=service_name,
                        role=role,
                        path=path,
                    )
                    return JSONResponse(
                        {
                            "ok": False,
                            "data": None,
                            "error": {
                                "code": "tenant_claim_required",
                                "message": "service token is missing the record_tenant_id claim",
                                "details": {},
                            },
                            "trace_id": getattr(request.state, "trace_id", ""),
                        },
                        status_code=401,
                    )

                # Bot bypass flag is forwarded to the limiter so the counter
                # still tracks bypassed traffic (VIP visibility).
                _bot_bypass = False
                _req_body_data: dict[str, Any] = {}
                try:
                    body = await request.body()
                    if body:
                        _req_body_data = _json.loads(body)
                        _req_bot_id = _req_body_data.get("bot_id")
                        _req_channel = _req_body_data.get("channel_type", "web")
                        # mega-sprint-G7: 4-key bot cache lookup must mirror
                        # ``BotRegistryService._key`` writer shape
                        # ``ragbot:bot:{tenant}:{workspace}:{bot}:{channel}``;
                        # the prior 3-key shape always missed → bypass_rate_limit
                        # was silently broken for every bot.
                        # Workspace sourcing chain: request.state (if an upstream
                        # middleware bound it) → JSON body → fallback to
                        # ``str(record_tenant_id)`` per CLAUDE.md identity rule.
                        _req_workspace_id = (
                            getattr(request.state, "workspace_id", None)
                            or _req_body_data.get("workspace_id")
                            or (str(record_tenant_id) if record_tenant_id is not None else None)
                        )
                        if (
                            _req_bot_id
                            and record_tenant_id is not None
                            and _req_workspace_id
                        ):
                            _bot_cache_key = (
                                f"ragbot:bot:{record_tenant_id!s}"
                                f":{_req_workspace_id}"
                                f":{_req_bot_id}:{_req_channel}"
                            )
                            _cached = await redis.get(_bot_cache_key)
                            if _cached:
                                _bot_data = _json.loads(_cached)
                                if _bot_data.get("bypass_rate_limit", False):
                                    _bot_bypass = True
                except Exception:  # noqa: BLE001 — best-effort
                    pass

                # Layer 1: per-tenant rate limit. Bypass flags are forwarded
                # to ``limiter.check()`` so the Redis counter still INCRs
                # (VIP traffic visibility) while ``decision.allowed`` stays True.
                _bypass_rl = False
                if (
                    record_tenant_id is not None
                    and hasattr(container, "tenant_rate_limiter")
                ):
                    tenant_cfg = None
                    try:
                        cfg_cache = container.tenant_config_cache()
                        tenant_cfg = await cfg_cache.get(record_tenant_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("tenant_cfg_cache_lookup_skip", err=str(exc))
                    tenant_bypass = bool(
                        tenant_cfg.bypass_rate_limit if tenant_cfg else False,
                    )
                    tenant_limit = (
                        tenant_cfg.rate_limit_per_min if tenant_cfg else None
                    )
                    try:
                        limiter = container.tenant_rate_limiter()
                        sys_limit = await _get_sys_int_or_none(
                            redis, "tenant_rate_limit_per_min",
                        )
                        decision = await limiter.check(
                            record_tenant_id=record_tenant_id,
                            tenant_bypass=tenant_bypass,
                            bot_bypass=_bot_bypass,
                            tenant_limit=tenant_limit,
                            system_limit=sys_limit,
                        )
                    except Exception as exc:  # noqa: BLE001 — fail-closed (master Finding #6)
                        # Previously this silently swallowed all backend errors
                        # and let the request through (fail-open) — a tenant
                        # whose limiter was down could DoS the platform without
                        # any 429. Layer-1.5 (per-service) and Layer-2 (per-user)
                        # already fail closed; bring Layer-1 in line.
                        #
                        # Exception: owner / super_admin tokens (platform-internal
                        # control plane) keep their pass-through behaviour during
                        # a backend outage. Without this carve-out a Redis hiccup
                        # would also black out admin/ops APIs that need to fix it
                        # — mirroring the Layer-1.5 ``rl_val == 0`` owner skip.
                        if role in ("owner", "super_admin"):
                            logger.warning(
                                "tenant_rate_limiter_skip_owner_role",
                                err=str(exc),
                                error_type=type(exc).__name__,
                                role=role,
                            )
                            decision = None
                        else:
                            logger.warning(
                                "tenant_rate_limiter_fail_closed",
                                err=str(exc),
                                error_type=type(exc).__name__,
                                record_tenant_id=str(record_tenant_id),
                            )
                            rate_limit_fail_closed_total.labels(scope="tenant").inc()
                            return JSONResponse(
                                {
                                    "ok": False,
                                    "error": {
                                        "code": "RATE_LIMIT_UNAVAILABLE",
                                        "message": "rate-limit backend unavailable",
                                    },
                                },
                                status_code=503,
                                headers={
                                    "Retry-After": str(
                                        DEFAULT_RL_FAIL_CLOSED_RETRY_S,
                                    ),
                                },
                            )
                    if decision is not None:
                        # Bypass propagates downstream so Layer-1.5
                        # (per-service) and Layer-2 (per-user) skip too.
                        if decision.bypass:
                            _bypass_rl = True
                        if not decision.allowed:
                            return JSONResponse(
                                {
                                    "ok": False,
                                    "error": {
                                        "code": "tenant_rate_limit_exceeded",
                                        "message": (
                                            f"tenant rate limit "
                                            f"{decision.limit} req/{decision.window_s}s exceeded"
                                        ),
                                        "details": {"source": decision.source},
                                    },
                                },
                                status_code=429,
                            )
                elif _bot_bypass:
                    # No tenant context (or limiter unavailable) — but
                    # bot bypass still gates downstream layers.
                    _bypass_rl = True

                # Layer 1.5: per-service-token cap. Fails CLOSED on Redis outage.
                if not _bypass_rl and rl_val and rl_val > 0:
                    try:
                        exceeded = await _check_rate_limit(redis, service_name, rl_val, rl_win)
                    except RateLimitBackendUnavailable:
                        rate_limit_fail_closed_total.labels(scope="service").inc()
                        return JSONResponse(
                            {"ok": False, "error": {"code": "RATE_LIMIT_UNAVAILABLE",
                                                     "message": "rate-limit backend unavailable"}},
                            status_code=503,
                        )
                    if exceeded:
                        return JSONResponse(
                            {"ok": False, "error": {"code": "RATE_LIMITED",
                                                     "message": f"service rate limit {rl_val} req/{rl_win}s exceeded"}},
                            status_code=429,
                        )

                # Layer 2: per-user cap keyed on connect_id.
                _connect_id = _req_body_data.get("connect_id") if _req_body_data else None
                if not _bypass_rl and _connect_id:
                    try:
                        # CLAUDE.md Async Rule 1 — if neither knob has a
                        # payload override, the two Redis reads are
                        # independent; gather to halve middleware latency
                        # on the hot path. When one (or both) carries an
                        # override, that branch is sync — no gather needed.
                        _val_override = payload.get("per_user_rl_val")
                        _win_override = payload.get("per_user_rl_win")
                        if _val_override is None and _win_override is None:
                            _val_raw, _win_raw = await asyncio.gather(
                                _get_sys_int(redis, "rate_limit_per_user_value", 5),
                                _get_sys_int(redis, "rate_limit_per_user_window_s", 3),
                            )
                            per_user_val = int(_val_raw)
                            per_user_win = int(_win_raw)
                        else:
                            per_user_val = int(
                                _val_override
                                if _val_override is not None
                                else await _get_sys_int(redis, "rate_limit_per_user_value", 5)
                            )
                            per_user_win = int(
                                _win_override
                                if _win_override is not None
                                else await _get_sys_int(redis, "rate_limit_per_user_window_s", 3)
                            )
                    except Exception:  # noqa: BLE001
                        per_user_val, per_user_win = 5, 3
                    if per_user_val > 0:
                        user_key = f"{service_name}:user:{_connect_id}"
                        try:
                            exceeded = await _check_rate_limit(redis, user_key, per_user_val, per_user_win)
                        except RateLimitBackendUnavailable:
                            rate_limit_fail_closed_total.labels(scope="user").inc()
                            return JSONResponse(
                                {"ok": False, "error": {"code": "RATE_LIMIT_UNAVAILABLE",
                                                         "message": "rate-limit backend unavailable"}},
                                status_code=503,
                            )
                        if exceeded:
                            return JSONResponse(
                                {"ok": False, "error": {"code": "RATE_LIMITED_USER",
                                                         "message": f"per-user rate limit {per_user_val} req/{per_user_win}s exceeded"}},
                                status_code=429,
                            )

                # Bind to request.state. record_tenant_id may still be None when
                # role ∈ {owner, super_admin} ran an unscoped admin endpoint.
                request.state.record_tenant_id = (
                    record_tenant_id or _SYSTEM_TENANT_ID
                )
                request.state.user_id = service_name
                request.state.bot_id = None
                request.state.role = role
                bind_request_context(
                    record_tenant_id=str(request.state.record_tenant_id),
                    user_id=service_name,
                )
                _service_jwt_ok = True
        except Exception as exc:  # noqa: BLE001 — fall through to user JWT path
            logger.debug("service_jwt_verify_skipped", err=str(exc))

        if _service_jwt_ok:
            return await call_next(request)

        # Fallback: user JWT (RS256).
        try:
            container = request.app.state.container
            verifier = container.jwt_verifier()
            claims = verifier.verify(token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("jwt_verify_failed", err=str(exc))
            return _unauth("invalid token", request)

        record_tenant_id: UUID | None = None
        rt_claim = claims.get("record_tenant_id")
        if rt_claim:
            try:
                record_tenant_id = UUID(str(rt_claim))
            except (TypeError, ValueError):
                record_tenant_id = None
        if record_tenant_id is None:
            # Legacy user-JWT carrying tenant_id directly as UUID string.
            try:
                record_tenant_id = UUID(str(claims["tenant_id"]))
            except (KeyError, ValueError):
                return _unauth("missing record_tenant_id claim", request)

        bot_id = claims.get("bot_id")
        user_id = claims.get("sub") or claims.get("user_id")

        request.state.record_tenant_id = record_tenant_id
        request.state.user_id = str(user_id) if user_id else None
        request.state.bot_id = UUID(str(bot_id)) if bot_id else None
        request.state.role = claims.get("role", "user")

        bind_request_context(
            record_tenant_id=str(record_tenant_id),
            bot_id=str(bot_id) if bot_id else None,
            user_id=str(user_id) if user_id else None,
        )

        return await call_next(request)


async def _get_sys_int(redis_client: Any, key: str, default: int) -> int:
    try:
        raw = await redis_client.get(f"ragbot:sysconfig:{key}")
        if raw is not None:
            val = _json.loads(raw)
            return int(val)
    except Exception:  # noqa: BLE001
        pass
    return default


async def _get_sys_int_or_none(redis_client: Any, key: str) -> int | None:
    try:
        raw = await redis_client.get(f"ragbot:sysconfig:{key}")
        if raw is not None:
            return int(_json.loads(raw))
    except Exception:  # noqa: BLE001
        pass
    return None


async def _check_rate_limit(redis_client: Any, service_name: str, limit: int, window: int) -> bool:
    try:
        now = int(time.time())
        bucket = now // window
        key = f"{_RL_PREFIX}{service_name}:{bucket}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window + 1)
        return count > limit
    except Exception as exc:  # noqa: BLE001
        rate_limit_backend_error_total.labels(reason="redis_error").inc()
        raise RateLimitBackendUnavailable(str(exc)) from exc


def _unauth(reason: str, request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "data": None,
            "error": {"code": "UNAUTHORIZED", "message": reason, "details": {}},
            "trace_id": getattr(request.state, "trace_id", ""),
        },
        status_code=401,
    )


def enforce_tenant_match(request: Request, body_record_tenant_id: UUID) -> None:
    """Defense-in-depth route guard: JWT record_tenant_id must equal body record_tenant_id."""
    if check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL):
        return
    jwt_tid = getattr(request.state, "record_tenant_id", None)
    if jwt_tid is None:
        raise HTTPException(status_code=403, detail="missing tenant context")
    try:
        if UUID(str(jwt_tid)) != UUID(str(body_record_tenant_id)):
            logger.warning(
                "record_tenant_id_mismatch_route_guard",
                jwt_record_tenant_id=str(jwt_tid),
                body_record_tenant_id=str(body_record_tenant_id),
                path=request.url.path,
            )
            raise HTTPException(status_code=403, detail="record_tenant_id mismatch")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=403, detail="invalid tenant context",
        ) from exc


__all__ = [
    "RateLimitBackendUnavailable",
    "TenantContextMiddleware",
    "enforce_tenant_match",
]
