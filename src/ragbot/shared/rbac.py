"""RBAC role levels — centralized constants.

Numeric level system (7-tier, gaps of 20 for future insertion):
100 = super_admin (platform), 80 = tenant (workspace owner),
60 = admin, 40 = operator, 20 = user, 10 = viewer, 0 = guest

Usage: ``require_min_level(request, 60)`` instead of
       ``role not in ("admin", "superadmin")``
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request

from ragbot.shared.errors import ForbiddenError

ROLE_LEVELS: dict[str, int] = {
    "super_admin": 100,
    "superadmin": 100,   # alias
    "platform_admin": 100,  # alias
    "owner": 100,  # alias used in test_chat _require_owner
    "tenant": 80,
    "tenant_admin": 80,  # alias from legacy
    "admin": 60,
    "operator": 40,
    "service": 60,       # service tokens = admin level
    "system": 100,       # system = super_admin level
    "user": 20,
    "viewer": 10,
    "guest": 0,
}


def get_role_level(role: str) -> int:
    """Get numeric level for a role string. Unknown roles = 0 (guest)."""
    return ROLE_LEVELS.get(role, 0)


def check_min_level(request: Request, min_level: int) -> bool:
    """Check if request has at least *min_level* permission."""
    role = getattr(request.state, "role", "guest")
    return get_role_level(role) >= min_level


def require_min_level(request: Request, min_level: int) -> None:
    """Raise :class:`ForbiddenError` when the caller lacks permission."""
    if not check_min_level(request, min_level):
        raise ForbiddenError(f"Insufficient permission (requires level {min_level})")


def require_min_level_dep(
    min_level: int,
) -> Callable[[Request], Awaitable[None]]:
    """Return a FastAPI dependency enforcing a minimum numeric role level.

    Mirrors :func:`require_min_level` but as a declarable dependency, so a
    router mount can be gated fail-closed as a unit::

        router.include_router(
            test_chat.router,
            dependencies=[Depends(require_min_level_dep(100))],
        )

    Used to enforce the internal test-harness boundary at the route/auth layer
    rather than relying solely on a network gateway. The closure name surfaces
    the required level in OpenAPI / debug traces.
    """

    async def _dep(request: Request) -> None:
        require_min_level(request, min_level)

    _dep.__name__ = f"require_min_level_{min_level}"
    return _dep


__all__ = [
    "ROLE_LEVELS",
    "check_min_level",
    "get_role_level",
    "require_min_level",
    "require_min_level_dep",
]
