"""RBAC test fixtures (red-team matrix support).

Provides:
- ``ROLES`` / ``ROLE_LEVELS`` — domain-neutral role list (DB-canonical).
- ``make_request_for_role`` — builds a stand-in Starlette ``Request`` with
  ``request.state.role`` set, plus a real container (Redis + session_factory)
  so ``require_permission`` can hit the live ``module_permissions`` table.
- ``issue_service_jwt`` — mints an HS256 service JWT against the real
  ``api_tokens`` table for the few full-stack tampering tests that need to
  exercise the middleware layer end-to-end.

Levels are imported from ``shared.rbac`` (DB-canonical source). NEVER inline
literal levels in tests — always go through ``ROLE_LEVELS``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import jwt as pyjwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.cache.redis_cache import create_redis_client
from ragbot.shared.rbac import ROLE_LEVELS as _CANON_LEVELS

# Public, ordered list of the 7 canonical roles tested by the matrix.
# Aliases (superadmin/owner/system/service/tenant_admin) are intentionally
# excluded — they map to the same level and would just multiply the matrix
# without exercising new permission semantics.
ROLES: tuple[str, ...] = (
    "guest",
    "viewer",
    "user",
    "operator",
    "admin",
    "tenant",
    "super_admin",
)

# Re-export DB-canonical levels (no inline magic numbers in tests).
ROLE_LEVELS: dict[str, int] = {role: _CANON_LEVELS[role] for role in ROLES}


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    raise RuntimeError("DATABASE_URL env required for RBAC integration tests")


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


@dataclass
class FakeContainer:
    """Stand-in for the DI container exposing only the methods RBAC uses."""

    _session_factory: Any
    _redis_client: Any
    _extra: dict[str, Any] = field(default_factory=dict)

    def session_factory(self) -> Any:
        return self._session_factory

    def redis_client(self) -> Any:
        return self._redis_client


def make_request_for_role(
    role: str,
    *,
    session_factory: Any,
    redis_client: Any,
    tenant_id: int | None = None,
) -> Any:
    """Build a request-shaped object that satisfies ``require_permission``.

    The middleware contract is: ``request.state.role`` (str) + container
    accessor on ``request.app.state.container``. We honour exactly that
    surface — no FastAPI / TestClient boot needed for the matrix test loop.
    """
    container = FakeContainer(
        _session_factory=session_factory,
        _redis_client=redis_client,
    )
    state = SimpleNamespace(
        role=role,
        tenant_id=tenant_id,
        user_id=f"test-user-{role}",
        bot_id=None,
    )
    app_state = SimpleNamespace(container=container)
    app = SimpleNamespace(state=app_state)
    return SimpleNamespace(state=state, app=app)


def make_engine_and_factory() -> tuple[Any, Any]:
    """Create a fresh async engine + sessionmaker for one test run."""
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sf


def make_redis() -> Any:
    return create_redis_client(_redis_url())


def issue_service_jwt(
    *,
    service_name: str,
    role: str,
    tenant_id: int | None,
    secret: str,
    version: int = 1,
) -> str:
    """Mint a service JWT identical to ``JwtTokenService.create_token``.

    Caller must have inserted the matching ``api_tokens`` row first
    (otherwise the middleware will reject the version check).
    """
    # ``exp`` is REQUIRED by ``_decode``. Mirror
    # ``JwtTokenService.create_token`` which bakes ``iat`` + ``exp`` into
    # every minted token (default TTL = ``DEFAULT_JWT_TTL_S``).
    import time as _time
    _now = int(_time.time())
    payload: dict[str, Any] = {
        "jti": str(uuid.uuid4()),
        "sub": service_name,
        "ver": version,
        "role": role,
        "rl_val": 0,           # owner / unlimited so RL doesn't shadow tests
        "rl_win": 60,
        "iat": _now,
        "exp": _now + 3600,    # 1h is plenty for a single test run
        "iss": "ragbot",
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return pyjwt.encode(payload, secret, algorithm="HS256")


async def insert_bot(
    sf: Any,
    *,
    record_tenant_id: uuid.UUID,
    workspace_id: str,
    bot_id: str,
    channel_type: str = "web",
    bot_name: str = "rbac-matrix bot",
) -> uuid.UUID:
    record_bot_id = uuid.uuid4()
    async with sf() as session:
        await session.execute(
            text(
                """
                INSERT INTO bots (id, record_tenant_id, workspace_id,
                    bot_id, channel_type, bot_name,
                    system_prompt, is_deleted, created_at, updated_at, setting_options)
                VALUES (:id, :rt, :ws, :bid, :ct, :name,
                    '', false, now(), now(), '{}'::jsonb)
                """,
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": workspace_id,
                "bid": bot_id,
                "ct": channel_type,
                "name": bot_name,
            },
        )
        await session.commit()
    return record_bot_id


async def delete_bots_by_slug(sf: Any, *, bot_id: str, channel_type: str) -> None:
    async with sf() as session:
        await session.execute(
            text("DELETE FROM bots WHERE bot_id = :b AND channel_type = :c"),
            {"b": bot_id, "c": channel_type},
        )
        await session.commit()


__all__ = [
    "ROLES",
    "ROLE_LEVELS",
    "FakeContainer",
    "make_request_for_role",
    "make_engine_and_factory",
    "make_redis",
    "issue_service_jwt",
    "insert_bot",
    "delete_bots_by_slug",
]
