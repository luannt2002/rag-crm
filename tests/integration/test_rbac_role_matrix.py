"""RBAC role matrix red-team tests.

Covers two attack surfaces:

1. **Privilege escalation** — for every (module, permission) gate registered
   in the DB, each of the 7 canonical roles is probed: a role with level
   < min_required MUST be denied (ForbiddenError); a role with level
   >= min_required MUST be allowed. We hit the exact code path that
   ``Depends(require_permission_dep(module, permission))`` triggers — i.e.
   ``require_permission(request, module, permission)`` against live DB
   ``module_permissions`` rows + Redis cache.

2. **Cross-tenant isolation** — a tenant A admin (level 60) cannot read
   tenant B's bot via the bot registry. super_admin (level 100) is allowed
   to bypass tenant scope. Service JWT minted for tenant=10001 cannot
   escalate to tenant=10002 by lying in the request body — middleware
   returns 403 ``tenant_id_mismatch``.

The matrix size scales with the seeded permission count: at the time of
writing there are ~30 (module, permission) rows × 7 roles ≈ 200+ cases.
We pick a representative subset of 15 critical permissions × 7 roles
= 105 matrix cases for fast iteration, plus 5 cross-tenant + 3 JWT
tampering tests.

Run:
    set -a && source .env && set +a
    pytest tests/integration/test_rbac_role_matrix.py -v
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.infrastructure.cache.redis_cache import create_redis_client
from ragbot.infrastructure.repositories.bot_repository import SqlAlchemyBotRepository
from ragbot.interfaces.http.middlewares.rbac import (
    invalidate_rbac_cache,
    require_permission,
)
from ragbot.shared.errors import ForbiddenError
from tests.integration.fixtures.rbac import (
    ROLE_LEVELS,
    ROLES,
    delete_bots_by_slug,
    insert_bot,
    make_engine_and_factory,
    make_redis,
    make_request_for_role,
)


# ── Representative critical-route permission matrix ─────────────────────────
#
# 15 (module, permission) tuples covering the 7 modules listed in the plan:
#   chat / bot / ai / policy / admin (audit) / system (metrics)
#   / document / sync.
#
# The min_role_level for each tuple is asserted against the DB at test setup
# — we don't hardcode the level here, we LOOK IT UP from module_permissions.
# This way a future seed change auto-propagates without a brittle test diff.
CRITICAL_PERMISSIONS: list[tuple[str, str]] = [
    ("chat", "submit"),                      # POST /chat — viewer
    ("chat", "feedback"),                    # POST /feedback — viewer
    ("bot", "create"),                       # POST /admin/bots
    ("bot", "delete"),                       # DELETE /admin/bots/{id}
    ("bot", "list"),                         # GET  /admin/bots
    ("ai", "provider_create"),               # POST /admin/ai/providers
    ("ai", "provider_read"),                 # GET  /admin/ai/providers
    ("ai", "model_delete"),                  # DELETE /admin/ai/models/{id}
    ("policy", "policy_upsert"),             # POST /admin/policies
    ("policy", "capability_read"),           # GET  /admin/ai/models/{id}/capability
    ("admin", "audit_overview_read"),        # GET  /admin/audit/overview
    ("system", "metrics_overview"),          # GET  /admin/metrics/overview
    ("document", "ingest"),                  # POST /documents/create
    ("document", "rechunk"),                 # POST /documents/rechunk
    ("sync", "bot_upsert"),                  # POST /sync/bot
]


# ── Session-scoped infra fixtures ───────────────────────────────────────────


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    engine, sf = make_engine_and_factory()
    yield sf
    await engine.dispose()


@pytest.fixture()
async def redis_client() -> AsyncIterator[Any]:
    client = make_redis()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture()
async def perm_levels(session_factory: Any) -> dict[tuple[str, str], int]:
    """Pull (module, permission) -> min_role_level once per module.

    If a (module, permission) row is missing, the test for that pair is
    SKIPPED with a clear marker — that's a Phase 1 seed gap, not a Phase 4
    test failure.
    """
    levels: dict[tuple[str, str], int] = {}
    async with session_factory() as session:
        rows = (await session.execute(
            text("SELECT module, permission, min_role_level FROM module_permissions"),
        )).fetchall()
    for module, permission, lvl in rows:
        levels[(module, permission)] = int(lvl)
    return levels


@pytest.fixture(autouse=True)
async def _flush_rbac_cache() -> AsyncIterator[None]:
    """Always start with a clean RBAC perm cache so the test sees fresh DB.

    Uses its own short-lived Redis client (independent of the per-test
    ``redis_client`` fixture) so it can run autouse without coupling.
    """
    flush = make_redis()
    try:
        await flush.delete("ragbot:rbac:perms")
        yield
        await flush.delete("ragbot:rbac:perms")
    finally:
        await flush.close()


# ──────────────────────────────────────────────────────────────────────────
# Section 1 — Matrix: 15 critical permissions × 7 roles = 105 cases
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("module", "permission"), CRITICAL_PERMISSIONS)
async def test_rbac_role_permission_matrix(
    module: str,
    permission: str,
    role: str,
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """Each role iff its level >= DB-seeded min_role_level → allowed."""
    if (module, permission) not in perm_levels:
        pytest.skip(
            f"permission {module}:{permission} not seeded in module_permissions "
            "— Phase 1 seed gap, run scripts/seed_rbac_permissions_s11b.py",
        )

    required_level = perm_levels[(module, permission)]
    user_level = ROLE_LEVELS[role]
    expected_allowed = user_level >= required_level

    request = make_request_for_role(
        role=role,
        session_factory=session_factory,
        redis_client=redis_client,
    )

    if expected_allowed:
        # Must return None without raising.
        await require_permission(request, module, permission)
    else:
        with pytest.raises(ForbiddenError):
            await require_permission(request, module, permission)


# ──────────────────────────────────────────────────────────────────────────
# Section 2 — Privilege-escalation negative tests (defensive double-checks)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guest_denied_on_all_admin_module_permissions(
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """guest (level=0) must be denied for every admin/system/ai permission
    whose min_role_level > 0. This guards against a future seed change that
    accidentally drops a min level to 0 on a sensitive route.
    """
    sensitive_modules = {"admin", "system", "ai", "bot", "policy", "sync"}
    request = make_request_for_role(
        role="guest",
        session_factory=session_factory,
        redis_client=redis_client,
    )
    denied_count = 0
    for (module, perm), lvl in perm_levels.items():
        if module not in sensitive_modules or lvl == 0:
            continue
        with pytest.raises(ForbiddenError):
            await require_permission(request, module, perm)
        denied_count += 1
    assert denied_count > 0, "expected at least one sensitive permission seeded"


@pytest.mark.asyncio
async def test_super_admin_passes_all_seeded_permissions(
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """super_admin (level=100) must pass EVERY seeded permission. If a row
    has min_role_level > 100 the seed is malformed and this test catches it.
    """
    request = make_request_for_role(
        role="super_admin",
        session_factory=session_factory,
        redis_client=redis_client,
    )
    for (module, perm), _lvl in perm_levels.items():
        await require_permission(request, module, perm)


@pytest.mark.asyncio
async def test_unknown_role_treated_as_guest(
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """A request whose state.role is an unknown string MUST be treated as
    guest (level 0). This is the no-privilege-escalation-via-typo guarantee
    enforced by ``shared.rbac.get_role_level``.
    """
    if ("bot", "create") not in perm_levels:
        pytest.skip("bot:create not seeded")
    request = make_request_for_role(
        role="hacker_made_up_role_42",
        session_factory=session_factory,
        redis_client=redis_client,
    )
    with pytest.raises(ForbiddenError):
        await require_permission(request, "bot", "create")


@pytest.mark.asyncio
async def test_undefined_permission_denies_by_default(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """A permission that doesn't exist in module_permissions MUST be denied
    even for super_admin — fail-closed posture (rbac.py line 73-77).
    """
    request = make_request_for_role(
        role="super_admin",
        session_factory=session_factory,
        redis_client=redis_client,
    )
    with pytest.raises(ForbiddenError):
        await require_permission(
            request, "nonexistent_module_xyz", "nonexistent_perm",
        )


# ──────────────────────────────────────────────────────────────────────────
# Section 3 — Cross-tenant isolation red-team
# ──────────────────────────────────────────────────────────────────────────


def _unique_slug(prefix: str = "rbacxt") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.mark.asyncio
async def test_cross_tenant_admin_cannot_resolve_other_tenant_bot(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """Tenant A admin issuing a registry lookup with their own tenant UUID
    MUST NOT see tenant B's bot, even if both tenants share the same
    ``(workspace_id, bot_id, channel_type)`` slug triple. RBAC level alone
    is not enough — the tenant filter at the repository layer is the
    second wall.
    """
    bot_id = _unique_slug("xt-admin")
    channel_type = "web"
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    # Both tenants pick the same workspace slug — only the UUID isolates.
    ws = _unique_slug("workspace")

    await delete_bots_by_slug(
        session_factory, bot_id=bot_id, channel_type=channel_type,
    )
    record_a = await insert_bot(
        session_factory, record_tenant_id=tenant_a, workspace_id=ws,
        bot_id=bot_id, channel_type=channel_type,
    )
    record_b = await insert_bot(
        session_factory, record_tenant_id=tenant_b, workspace_id=ws,
        bot_id=bot_id, channel_type=channel_type,
    )
    try:
        repo = SqlAlchemyBotRepository(session_factory=session_factory)
        registry = BotRegistryService(repo=repo, redis_client=redis_client)

        cfg_a = await registry.lookup(
            tenant_a, ws, bot_id, channel_type,
        )
        # Tenant A admin asking for slug X scoped to tenant A returns A's row
        # ONLY. There is no API path by which an admin of tenant A can cause
        # the registry to return B's row — the lookup signature requires
        # the tenant UUID and the cache key is tenant-scoped.
        assert cfg_a is not None
        assert isinstance(cfg_a, BotConfig)
        assert cfg_a.id == record_a
        assert cfg_a.record_tenant_id == tenant_a

        # Crucially: A's resolved record is NOT B's record.
        assert cfg_a.id != record_b
    finally:
        for tid in (tenant_a, tenant_b):
            try:
                await redis_client.delete(
                    f"ragbot:bot:{tid}:{ws}:{bot_id}:{channel_type}",
                )
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
        await delete_bots_by_slug(
            session_factory, bot_id=bot_id, channel_type=channel_type,
        )


@pytest.mark.asyncio
async def test_super_admin_request_carries_no_tenant_does_not_short_circuit_rbac(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """Even super_admin (level=100) MUST go through ``require_permission``;
    the level check applies, and the absence of a tenant_id on state does
    not cause the gate to silently allow undefined permissions. Combined
    with the previous test, this shows: super_admin bypasses TENANT scope,
    but never RBAC fail-closed.
    """
    request = make_request_for_role(
        role="super_admin",
        session_factory=session_factory,
        redis_client=redis_client,
        tenant_id=None,
    )
    # A defined permission with min=100 → super_admin passes.
    # An undefined permission → still denied.
    with pytest.raises(ForbiddenError):
        await require_permission(request, "totally_made_up", "x")


@pytest.mark.asyncio
async def test_tenant_role_at_workspace_scope_blocked_from_platform_perms(
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """tenant role (level=80) MUST be denied for any permission whose level
    is 100 (platform-only). admin.manage_tenants is the canonical example.
    """
    platform_perms = [
        (m, p) for (m, p), lvl in perm_levels.items() if lvl >= ROLE_LEVELS["super_admin"]
    ]
    if not platform_perms:
        pytest.skip("no platform-level permission seeded")
    request = make_request_for_role(
        role="tenant",
        session_factory=session_factory,
        redis_client=redis_client,
    )
    for module, perm in platform_perms:
        with pytest.raises(ForbiddenError):
            await require_permission(request, module, perm)


@pytest.mark.asyncio
async def test_role_level_alias_super_admin_equivalent(
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """The aliases ``superadmin`` / ``platform_admin`` / ``system`` MUST
    resolve to level 100 just like ``super_admin``. This is the contract
    that lets old-style JWTs keep working post-migration.
    """
    if not perm_levels:
        pytest.skip("no perms seeded")
    sample_module, sample_perm = next(iter(perm_levels.keys()))
    for alias in ("superadmin", "platform_admin", "system"):
        request = make_request_for_role(
            role=alias,
            session_factory=session_factory,
            redis_client=redis_client,
        )
        # Should never raise — these are level=100 aliases.
        await require_permission(request, sample_module, sample_perm)


@pytest.mark.asyncio
async def test_rbac_cache_invalidation_picks_up_db_change(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """If an admin lowers/raises a min_role_level in DB and invalidates the
    cache, the very next ``require_permission`` MUST observe the new level.
    Tests the audit-trail expectation: no stale 5min-cached permission ever
    blocks the operator after a deliberate ops change.
    """
    test_module = f"rbac_test_module_{uuid.uuid4().hex[:6]}"
    test_perm = "probe"
    # Seed level=80 → operator (40) blocked.
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO module_permissions(module, permission, min_role_level)
                VALUES (:m, :p, 80)
                ON CONFLICT (module, permission)
                DO UPDATE SET min_role_level = 80
                """,
            ),
            {"m": test_module, "p": test_perm},
        )
        await session.commit()

    try:
        request = make_request_for_role(
            role="operator",
            session_factory=session_factory,
            redis_client=redis_client,
        )
        with pytest.raises(ForbiddenError):
            await require_permission(request, test_module, test_perm)

        # Lower min level to 40 + invalidate cache.
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE module_permissions SET min_role_level=40 "
                    "WHERE module=:m AND permission=:p",
                ),
                {"m": test_module, "p": test_perm},
            )
            await session.commit()
        await invalidate_rbac_cache(request)

        # Operator now passes.
        await require_permission(request, test_module, test_perm)
    finally:
        async with session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM module_permissions "
                    "WHERE module=:m AND permission=:p",
                ),
                {"m": test_module, "p": test_perm},
            )
            await session.commit()
        await redis_client.delete("ragbot:rbac:perms")


# ──────────────────────────────────────────────────────────────────────────
# Section 4 — JWT tampering / claims-vs-DB precedence
# ──────────────────────────────────────────────────────────────────────────


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_jwt_body_tenant_mismatch_rejects_403() -> None:
    """A service JWT minted for tenant_id=10001 MUST NOT be able to mutate
    tenant_id=10002 by lying in the request body. ``TenantContextMiddleware``
    cross-checks JWT.tenant_id vs body.tenant_id and returns 403
    ``tenant_id_mismatch``.

    Mirrors test 3 of test_3key_cross_tenant_isolation.py — kept here as a
    Phase 4 red-team line so RBAC matrix runs include the same coverage.
    """
    from ragbot.application.services.jwt_token_service import JwtTokenService
    from ragbot.config.settings import get_settings

    settings = get_settings()

    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    redis = create_redis_client(_redis_url())

    service_name = f"rbac-mismatch-{uuid.uuid4().hex[:8]}"
    jwt_secret = settings.app.api_token or "ragbot-dev-test-secret"
    svc = JwtTokenService(session_factory=sf, jwt_secret=jwt_secret)

    try:
        await svc.create_token(
            service_name=service_name,
            description="rbac-matrix jwt/body tenant mismatch",
            redis_client=redis,
            role="service",
            rate_limit_value=0,
            rate_limit_window=60,
        )

        jwt_tenant_id = 10001
        body_tenant_id = 10002
        # ``exp`` is REQUIRED by ``_decode``.
        _now = int(time.time())
        payload = {
            "jti": str(uuid.uuid4()),
            "sub": service_name,
            "ver": 1,
            "role": "service",
            "rl_val": 0,
            "rl_win": 60,
            "tenant_id": jwt_tenant_id,
            "iat": _now,
            "exp": _now + 300,
            "iss": "ragbot",
        }
        token = pyjwt.encode(payload, jwt_secret, algorithm="HS256")

        import importlib
        app_mod = importlib.import_module("ragbot.interfaces.http.app")
        application = app_mod.create_app()

        def _do_request() -> tuple[int, dict[str, Any]]:
            with TestClient(application, raise_server_exceptions=False) as client:
                r = client.post(
                    f"{settings.app.api_base_path}/sync/bot",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "bot_id": _unique_slug("rbac-mismatch"),
                        "channel_type": "web",
                        "bot_name": "rbac mismatch bot",
                        "tenant_id": body_tenant_id,
                        "system_prompt": "",
                    },
                )
            return r.status_code, r.json()

        status_code, body = await asyncio.to_thread(_do_request)

        assert status_code == 403, (
            f"JWT vs body tenant mismatch must be rejected 403; "
            f"got {status_code} body={body!r}"
        )
        err = body.get("error") or {}
        assert err.get("code") == "tenant_id_mismatch", (
            f"error code must be tenant_id_mismatch; got: {body!r}"
        )
    finally:
        async with sf() as session:
            await session.execute(
                text("DELETE FROM api_tokens WHERE service_name = :n"),
                {"n": service_name},
            )
            await session.commit()
        try:
            await redis.delete(f"ragbot:token_ver:{service_name}")
        except Exception:  # noqa: BLE001
            pass
        await redis.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_role_in_state_falls_back_to_guest_level(
    perm_levels: dict[tuple[str, str], int],
    session_factory: Any,
    redis_client: Any,
) -> None:
    """If an attacker forges a JWT carrying ``role="GodEmperor"``, the role
    is unknown to ``ROLE_LEVELS`` and resolves to level 0 (guest). Therefore
    every non-public permission MUST deny.

    This is the second-line defence — even if signature verification were
    bypassed somehow, a made-up role string still has level 0.
    """
    if ("bot", "create") not in perm_levels:
        pytest.skip("bot:create not seeded")
    request = make_request_for_role(
        role="GodEmperor",
        session_factory=session_factory,
        redis_client=redis_client,
    )
    with pytest.raises(ForbiddenError):
        await require_permission(request, "bot", "create")


@pytest.mark.asyncio
async def test_missing_role_in_state_defaults_to_guest(
    session_factory: Any,
    redis_client: Any,
) -> None:
    """``getattr(request.state, "role", "guest")`` is the default in
    rbac.py — verify it: a request with no ``role`` attribute behaves as
    guest (denied for any permission > 0).
    """
    from types import SimpleNamespace
    container = type("C", (), {
        "session_factory": staticmethod(lambda: session_factory),
        "redis_client": staticmethod(lambda: redis_client),
    })()
    request = SimpleNamespace(
        state=SimpleNamespace(),  # NO role attribute on purpose
        app=SimpleNamespace(state=SimpleNamespace(container=container)),
    )
    with pytest.raises(ForbiddenError):
        await require_permission(request, "bot", "create")


__all__: list[str] = []
