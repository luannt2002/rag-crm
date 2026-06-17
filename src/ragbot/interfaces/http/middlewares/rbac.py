"""RBAC middleware — metadata-driven permission checks.

Uses role_definitions + module_permissions from DB (Redis cached 5min).
No hardcoded role strings — all checks via numeric level or module permission.

Single-flight on the permissions cache. The permission table
is small + global (one row per ``module:permission``), so the cache
key is a single Redis key (``ragbot:rbac:perms``). Without
single-flight, N concurrent first-requests after a cache flush all
fire ``SELECT * FROM module_permissions`` in parallel; with the
in-process ``AsyncSingleFlight`` (label ``rbac``) only one query runs
and the rest wait + re-read Redis. Wired against the module-level
singleton so all callers in a process share one lock.

Two usage modes:

1. Inside a route body (imperative):

    from ragbot.interfaces.http.middlewares.rbac import require_permission

    @router.post("/bots")
    async def create_bot(request: Request):
        await require_permission(request, "bot", "create")
        ...

2. As a FastAPI dependency (declarative — preferred for wiring):

    from fastapi import Depends
    from ragbot.interfaces.http.middlewares.rbac import require_permission_dep

    @router.post(
        "/bots",
        dependencies=[Depends(require_permission_dep("bot", "create"))],
    )
    async def create_bot(request: Request): ...
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import structlog
from starlette.requests import Request

from ragbot.shared.constants import DEFAULT_RBAC_CACHE_TTL_S
from ragbot.shared.errors import ForbiddenError
from ragbot.shared.rbac import get_role_level
from ragbot.shared.single_flight import AsyncSingleFlight

logger = structlog.get_logger(__name__)

_CACHE_PREFIX = "ragbot:rbac:perms"
_SINGLE_FLIGHT_LABEL = "rbac"

# Module-level singleton — every request handler in the process shares
# one ``AsyncSingleFlight`` instance so concurrent misses across
# requests coalesce into a single DB query. Bounded internally by
# ``DEFAULT_SINGLE_FLIGHT_MAX_LOCKS`` (here we only ever insert one key
# so the bound is academic).
_RBAC_SINGLE_FLIGHT: AsyncSingleFlight = AsyncSingleFlight(_SINGLE_FLIGHT_LABEL)


async def _read_cache(redis: Any) -> dict[str, int] | None:
    """Read + decode the permissions cache; ``None`` on miss / decode error."""
    cached = await redis.get(_CACHE_PREFIX)
    if not cached:
        return None
    try:
        decoded = json.loads(cached)
    except (ValueError, TypeError) as exc:
        logger.warning("rbac_cache_decode_error", err=str(exc))
        return None
    if not isinstance(decoded, dict):
        return None
    # Coerce keys/values defensively — they were JSON-roundtripped.
    return {str(k): int(v) for k, v in decoded.items()}


async def _query_db_and_cache(request: Request, redis: Any) -> dict[str, int]:
    """DB fallback + Redis back-fill — split for single-flight reuse."""
    sf = request.app.state.container.session_factory()
    async with sf() as session:
        from sqlalchemy import text
        rows = (await session.execute(
            text("SELECT module, permission, min_role_level FROM module_permissions"),
        )).fetchall()

    perms = {f"{r[0]}:{r[1]}": int(r[2]) for r in rows}
    await redis.set(_CACHE_PREFIX, json.dumps(perms), ex=DEFAULT_RBAC_CACHE_TTL_S)
    return perms


async def _load_permissions(request: Request) -> dict[str, int]:
    """Load module_permissions from Redis cache or DB.

    Returns dict: "module:permission" → min_role_level

    Single-flight: when N concurrent callers see a cold cache,
    only one fires the DB query — the rest wait for the in-flight lock
    then re-read Redis (which the writer back-fills). On lock-wait
    timeout the waiter falls back to its own DB query — better a
    duplicate query than a hung request.
    """
    redis = request.app.state.container.redis_client()

    cached = await _read_cache(redis)
    if cached is not None:
        return cached

    sf_lock = await _RBAC_SINGLE_FLIGHT.get_lock(_CACHE_PREFIX)
    if not sf_lock.locked():
        async with sf_lock:
            cached = await _read_cache(redis)
            if cached is not None:
                return cached
            return await _query_db_and_cache(request, redis)

    acquired = await _RBAC_SINGLE_FLIGHT.wait_for_lock_release(sf_lock)
    if not acquired:
        return await _query_db_and_cache(request, redis)
    try:
        cached = await _read_cache(redis)
        if cached is not None:
            return cached
        return await _query_db_and_cache(request, redis)
    finally:
        sf_lock.release()


async def require_permission(request: Request, module: str, permission: str) -> None:
    """Check if current user has permission for module:action.

    Raises ForbiddenError if insufficient permission.
    """
    role = getattr(request.state, "role", "guest")
    user_level = get_role_level(role)

    perms = await _load_permissions(request)
    key = f"{module}:{permission}"
    required_level = perms.get(key)

    if required_level is None:
        # Permission not defined → deny by default (safe)
        logger.warning("rbac_permission_not_defined", module=module, permission=permission)
        raise ForbiddenError(f"Permission {key} not configured")

    if user_level < required_level:
        logger.info(
            "rbac_denied",
            role=role,
            user_level=user_level,
            required=required_level,
            module=module,
            permission=permission,
        )
        raise ForbiddenError(
            f"Insufficient permission: {key} requires level {required_level}, you have {user_level}",
        )


def require_permission_dep(
    module: str, permission: str,
) -> Callable[[Request], Awaitable[None]]:
    """Return a FastAPI dependency that enforces ``module:permission``.

    The factory captures ``module`` + ``permission`` so each route declares
    its own granular gate via:

        dependencies=[Depends(require_permission_dep("ai", "provider_create"))]

    Behaviour matches :func:`require_permission` (raises ForbiddenError when
    the caller's role level is below the seed level).
    """

    async def _dep(request: Request) -> None:
        await require_permission(request, module, permission)

    # Give the closure a stable, readable name so OpenAPI / debug traces
    # show the gated permission instead of an opaque ``_dep``.
    _dep.__name__ = f"require_{module}_{permission}"
    return _dep


async def invalidate_rbac_cache(request: Request) -> None:
    """Clear RBAC cache — call after permission changes."""
    redis = request.app.state.container.redis_client()
    await redis.delete(_CACHE_PREFIX)


__all__ = ["invalidate_rbac_cache", "require_permission", "require_permission_dep"]
