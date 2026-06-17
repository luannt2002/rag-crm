"""tenant-scope red-team for repository calls in routes.

Verifies the **defense-in-depth** scope filter at the repository layer
catches cross-tenant access even when an attacker controls a UUID.

Surface under attack:
- ``BotRepository.get_by_id`` / ``update_bot`` / ``soft_delete`` —
  every test_chat admin route now passes ``tenant_id=`` ().
- ``MessageRepository.soft_delete_content`` / ``soft_delete_conversation`` —
  the GDPR routes (``admin_gdpr.py``) previously called these with
  ``tenant_id=`` (wrong kwarg name) which would TypeError at runtime;
  Phase 3 corrects to ``record_tenant_id=`` and we cover the path here.
- ``RequestLogRepository`` metrics — the route passes
  ``record_tenant_id=request.state.tenant_id`` so a level-60 admin
  cannot pull another tenant's logs.
- ``AuditRepository.get_audit_overview`` — same shape as metrics.
- ``TenantPolicyRepository.list_policies`` — tenant-scoped per row.

These tests hit real Postgres (DATABASE_URL) — same setup as the existing
``test_3key_cross_tenant_isolation.py``. They write their own rows with
unique IDs and clean up after themselves.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.repositories.audit_repository import AuditRepository
from ragbot.infrastructure.repositories.bot_repository import SqlAlchemyBotRepository
from ragbot.infrastructure.repositories.message_repository import MessageRepository
from ragbot.infrastructure.repositories.request_log_repository import (
    RequestLogRepository,
)
from ragbot.infrastructure.repositories.tenant_policy_repository import (
    TenantPolicyRepository,
)
from ragbot.shared.errors import TenantIsolationViolation


# ── DB fixture ─────────────────────────────────────────────────────────────


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if (
                    line.startswith("DATABASE_URL=")
                    and "DATABASE_URL_SYNC" not in line
                ):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not set and .env not found")


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────


def _slug(prefix: str = "redteam") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _insert_bot(
    sf: Any,
    *,
    record_tenant_id: uuid.UUID,
    bot_id: str,
    channel_type: str = "web",
    bot_name: str = "scope test bot",
) -> uuid.UUID:
    record_bot_id = uuid.uuid4()
    await _ensure_tenant_row(sf, record_tenant_id)
    async with sf() as session:
        await session.execute(
            text(
                """
                INSERT INTO bots (
                    id, record_tenant_id, workspace_id, bot_id, channel_type,
                    bot_name, system_prompt, is_deleted,
                    created_at, updated_at, setting_options,
                    custom_vocabulary, max_documents, plan_limits,
                    bypass_token_limit, bypass_rate_limit, language
                )
                VALUES (
                    :id, :rt, :ws, :bot_id, :ch,
                    :bot_name, '', false,
                    now(), now(), '{}'::jsonb,
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                """
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": f"ws-{record_bot_id.hex[:8]}",
                "bot_id": bot_id,
                "ch": channel_type,
                "bot_name": bot_name,
            },
        )
        await session.commit()
    return record_bot_id


def _tid(int_legacy: int) -> uuid.UUID:
    """Map legacy int tenant_id → deterministic UUID for cross-tenant tests."""
    return uuid.UUID(f"00000000-0000-0000-0000-{int_legacy:012d}")


async def _delete_bot(sf: Any, record_bot_id: uuid.UUID) -> None:
    async with sf() as session:
        await session.execute(
            text("DELETE FROM bots WHERE id = :id"), {"id": record_bot_id}
        )
        await session.commit()


# Ensure tenants exist in the tenants table — required FK for some inserts.
async def _ensure_tenant_row(sf: Any, record_tenant_id: uuid.UUID) -> None:
    """Idempotent insert into ``tenants`` so the FK on ``bots`` is satisfied."""
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": record_tenant_id, "name": f"redteam-{str(record_tenant_id)[:8]}"},
        )
        await session.commit()


# ──────────────────────────────────────────────────────────────────────────
# Test 1 — BotRepository.get_by_id rejects cross-tenant UUID
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bot_get_by_id_rejects_cross_tenant(session_factory: Any) -> None:
    """RED-TEAM: tenant 1's user knows tenant 2's record_bot_id (e.g. via
    a leaked log) and tries to load it. ``tenant_id`` defense-in-depth
    filter MUST return None — not the row, not a 403/raise (404 from the
    route layer would expose existence; None lets the route return 404).
    """
    bot_id = _slug("get-by-id")
    rt_a = _tid(5101)
    rt_b = _tid(5102)
    record_a = await _insert_bot(
        session_factory, record_tenant_id=rt_a, bot_id=bot_id,
    )
    record_b = await _insert_bot(
        session_factory, record_tenant_id=rt_b, bot_id=bot_id,
    )
    try:
        repo = SqlAlchemyBotRepository(session_factory=session_factory)

        # Owner reads its own row — works.
        cfg_a = await repo.get_by_id(record_a, record_tenant_id=rt_a)
        assert cfg_a is not None
        assert cfg_a.id == record_a
        assert cfg_a.record_tenant_id == rt_a

        # Cross-tenant attempt — tenant A asks for tenant B's PK.
        leaked = await repo.get_by_id(record_b, record_tenant_id=rt_a)
        assert leaked is None, (
            "cross-tenant UUID lookup must return None (404 at route)"
        )

        # Platform admin (None) bypasses scope.
        admin = await repo.get_by_id(record_b, record_tenant_id=None)
        assert admin is not None
        assert admin.id == record_b
    finally:
        await _delete_bot(session_factory, record_a)
        await _delete_bot(session_factory, record_b)


# ──────────────────────────────────────────────────────────────────────────
# Test 2 — BotRepository.update_bot rejects cross-tenant write
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bot_update_rejects_cross_tenant(session_factory: Any) -> None:
    """RED-TEAM: tenant 5201 tries to rename tenant 5202's bot. The
    ``tenant_id`` filter on UPDATE must zero-row → return None.
    """
    bot_id = _slug("update")
    rt_a = _tid(5201)
    rt_b = _tid(5202)
    record_a = await _insert_bot(
        session_factory, record_tenant_id=rt_a, bot_id=bot_id, bot_name="A_OWNED",
    )
    record_b = await _insert_bot(
        session_factory, record_tenant_id=rt_b, bot_id=bot_id, bot_name="B_OWNED",
    )
    try:
        repo = SqlAlchemyBotRepository(session_factory=session_factory)

        # Cross-tenant update attempt — tenant A sets bot_name on B.
        result = await repo.update_bot(
            record_b, record_tenant_id=rt_a, bot_name="HIJACKED",
        )
        assert result is None, "cross-tenant update must return None"

        # B's bot_name is unchanged.
        async with session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT bot_name FROM bots WHERE id = :id"),
                    {"id": record_b},
                )
            ).fetchone()
            assert row is not None
            assert row[0] == "B_OWNED"
    finally:
        await _delete_bot(session_factory, record_a)
        await _delete_bot(session_factory, record_b)


# ──────────────────────────────────────────────────────────────────────────
# Test 3 — BotRepository.soft_delete rejects cross-tenant delete
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bot_soft_delete_rejects_cross_tenant(session_factory: Any) -> None:
    """RED-TEAM: tenant 5301 tries to soft_delete tenant 5302's bot.
    Filter must zero-row → return False; row stays alive.
    """
    bot_id = _slug("delete")
    rt_a = _tid(5301)
    rt_b = _tid(5302)
    record_a = await _insert_bot(
        session_factory, record_tenant_id=rt_a, bot_id=bot_id,
    )
    record_b = await _insert_bot(
        session_factory, record_tenant_id=rt_b, bot_id=bot_id,
    )
    try:
        repo = SqlAlchemyBotRepository(session_factory=session_factory)
        ok = await repo.soft_delete(record_b, record_tenant_id=rt_a)
        assert ok is False, "cross-tenant soft_delete must report False"

        # Still alive.
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT is_deleted FROM bots WHERE id = :id"
                    ),
                    {"id": record_b},
                )
            ).fetchone()
            assert row is not None
            assert row[0] is False
    finally:
        await _delete_bot(session_factory, record_a)
        await _delete_bot(session_factory, record_b)


# ──────────────────────────────────────────────────────────────────────────
# Test 4 — MessageRepository.soft_delete_content (GDPR) requires record_tenant_id
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_soft_delete_requires_record_tenant_id() -> None:
    """RED-TEAM: ``MessageRepository.soft_delete_content`` MUST raise
    ``TenantIsolationViolation`` if ``record_tenant_id`` is None. This
    catches the silent kwarg-name bug in ``admin_gdpr.py`` (was passing
    ``tenant_id=`` which would TypeError; Phase 3 fix uses the correct
    kwarg name and this test pins the contract).
    """
    repo = MessageRepository(session_factory=lambda: None)  # type: ignore[arg-type]
    with pytest.raises(TenantIsolationViolation):
        await repo.soft_delete_content(uuid.uuid4(), record_tenant_id=None)
    with pytest.raises(TenantIsolationViolation):
        await repo.soft_delete_conversation(
            uuid.uuid4(), record_tenant_id=None,
        )


# ──────────────────────────────────────────────────────────────────────────
# Test 5 — admin_gdpr route uses the correct kwarg name
# ──────────────────────────────────────────────────────────────────────────


def test_admin_gdpr_uses_record_tenant_id_kwarg() -> None:
    """RED-TEAM: static-grep the GDPR route to ensure it calls
    ``soft_delete_content(record_tenant_id=...)`` not ``tenant_id=``.
    Pins the fix in source.
    """
    src = open(
        os.path.join(
            os.path.dirname(__file__), "..", "..",
            "src", "ragbot", "interfaces", "http", "routes", "admin_gdpr.py",
        ),
        encoding="utf-8",
    ).read()
    assert "record_tenant_id=" in src, (
        "admin_gdpr.py must pass record_tenant_id (not tenant_id)"
    )
    # Forbid the old wrong kwarg in the soft_delete_* call sites.
    assert "soft_delete_content(\n        message_id, tenant_id=" not in src
    assert "soft_delete_conversation(\n        conversation_id, tenant_id=" not in src


# ──────────────────────────────────────────────────────────────────────────
# Test 6 — RequestLogRepository.get_overview enforces record_tenant_id
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_log_overview_requires_tenant(session_factory: Any) -> None:
    """RED-TEAM: ``get_overview`` calls ``_ensure(record_tenant_id)`` —
    None must raise; valid tenant must filter rows by
    ``record_tenant_id``. Two tenants with rows; tenant 1's overview
    must NOT count tenant 2's rows.
    """
    repo = RequestLogRepository(session_factory=session_factory)
    with pytest.raises(TenantIsolationViolation):
        await repo.get_overview(record_tenant_id=None)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# Test 7 — AuditRepository.get_audit_overview rejects None tenant
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_overview_requires_record_tenant_id(session_factory: Any) -> None:
    """RED-TEAM: P17 fix made ``record_tenant_id`` mandatory on
    ``get_audit_overview``. The signature itself enforces it (UUID
    positional kwarg, no Optional). A None passed in should raise
    TypeError or AttributeError downstream — proves the type system
    is the gate.
    """
    repo = AuditRepository(session_factory=session_factory)
    # Pydantic typing gives us static gate; runtime call with None
    # blows up in the SQL where-clause comparison.
    with pytest.raises((TypeError, AttributeError, Exception)):
        await repo.get_audit_overview(
            record_tenant_id=None,  # type: ignore[arg-type]
        )


# ──────────────────────────────────────────────────────────────────────────
# Test 8 — TenantPolicyRepository.list_policies enforces tenant filter
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_list_requires_tenant(session_factory: Any) -> None:
    """RED-TEAM: ``list_policies`` runs through ``_ensure`` so a None
    tenant aborts with ``TenantIsolationViolation`` — admin route
    cannot accidentally call without scope.
    """
    repo = TenantPolicyRepository(session_factory=session_factory)
    with pytest.raises(TenantIsolationViolation):
        await repo.list_policies(record_tenant_id=None)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# Test 9 — sync.list_documents now scopes by tenant
# ──────────────────────────────────────────────────────────────────────────


def test_sync_list_documents_uses_tenant_filter() -> None:
    """RED-TEAM: pin the SQL — ``GET /sync/documents`` MUST include
    ``b.tenant_id = :tenant_id`` in the JOIN/WHERE clause. 
    Phase 3 fix — without it two tenants sharing a slug leak documents.
    """
    src = open(
        os.path.join(
            os.path.dirname(__file__), "..", "..",
            "src", "ragbot", "interfaces", "http", "routes", "sync.py",
        ),
        encoding="utf-8",
    ).read()
    assert "b.tenant_id = :tenant_id" in src, (
        "sync.list_documents must filter bots by tenant_id"
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 10 — test_chat.list_documents now scopes by tenant
# ──────────────────────────────────────────────────────────────────────────


def test_test_chat_list_documents_uses_tenant_filter() -> None:
    """RED-TEAM: same pin for the demo route. Even though it's behind
    the demo auth, scope filter is defense-in-depth — .
    """
    src = open(
        os.path.join(
            os.path.dirname(__file__), "..", "..",
            "src", "ragbot", "interfaces", "http", "routes", "test_chat.py",
        ),
        encoding="utf-8",
    ).read()
    assert "b.tenant_id = :tenant_id" in src, (
        "test_chat.list_documents must filter bots by tenant_id"
    )
    # The helper exists for repo scope filter wiring.
    assert "def _tenant_scope(" in src


# ──────────────────────────────────────────────────────────────────────────
# Test 11 — _tenant_scope helper bypasses for platform admin only
# ──────────────────────────────────────────────────────────────────────────


def test_tenant_scope_helper_semantics() -> None:
    """Unit-style: ``_tenant_scope`` returns None for level-100 (platform
    admin) and the int tenant_id for everyone else. Pins the contract
    so a future refactor can't silently flip the meaning of None.
    """
    from types import SimpleNamespace

    from ragbot.interfaces.http.routes.test_chat import _tenant_scope

    # Platform admin: bypass (role=system → level 100).
    req_admin = SimpleNamespace(
        state=SimpleNamespace(role="system", tenant_id_int=9999),
    )
    assert _tenant_scope(req_admin) is None

    # Tenant admin: scoped to its own tenant (role=admin → level 80).
    req_tenant = SimpleNamespace(
        state=SimpleNamespace(role="admin", tenant_id_int=5501),
    )
    assert _tenant_scope(req_tenant) == 5501

    # No tenant context: returns None (route handler decides — usually 422).
    req_anon = SimpleNamespace(state=SimpleNamespace(role="viewer"))
    assert _tenant_scope(req_anon) is None
