"""RLS tenant-isolation red-team — real Postgres, layer-2 + layer-3 live.

Row-level security only enforces when all three layers are live:

1. ``ENABLE/FORCE ROW LEVEL SECURITY`` + a GUC-driven ``CREATE POLICY``.
2. A **NOSUPERUSER + NOBYPASSRLS** role the query runs as. Under the admin
   (superuser) DSN the policies are bypassed, so these tests run their reads
   under ``SET LOCAL ROLE ragbot_app`` — the same NOBYPASSRLS role the
   runtime DSN will eventually authenticate as.
3. A per-transaction ``SET LOCAL app.tenant_id = '<uuid>'`` bind.

What this proves
~~~~~~~~~~~~~~~~
* GUC bound to tenant A → a query under the NOBYPASSRLS role returns ONLY
  tenant-A rows; tenant-B rows never leak.
* GUC NEVER bound (fresh transaction) → ZERO rows (fail-closed), and
  crucially the query does NOT raise. The RLS-2 fix
  (``rls_missing_ok_setting`` migration) makes ``document_service_index``
  read the tenant GUC with the ``missing_ok`` (``, true``) form, so an
  unset GUC yields NULL → no rows, instead of throwing
  ``unrecognized configuration parameter``.

Gated on the ``integration`` marker; reads ``DATABASE_URL`` from the env
(set by the harness or ``set -a && source .env && set +a``). Skips cleanly
when the DB is unreachable or the ``ragbot_app`` role is absent so unit-only
runs stay green.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


pytestmark = pytest.mark.integration


# The NOBYPASSRLS runtime role the reads run as (mirrors
# session.py::RUNTIME_DB_ROLE — a literal here keeps the test self-contained).
_APP_ROLE = "ragbot_app"


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    dsn = _database_url()
    if not dsn:
        pytest.skip("DATABASE_URL env var required for integration tests")
    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            role = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM pg_roles WHERE rolname = :r "
                        "AND rolbypassrls = false"
                    ),
                    {"r": _APP_ROLE},
                )
            ).first()
        if role is None:
            pytest.skip(
                f"{_APP_ROLE!r} NOBYPASSRLS role not provisioned — "
                "run the RLS role-grant migration first"
            )
    except (OperationalError, OSError):
        pytest.skip("Postgres unreachable for integration tests")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _seed_tenant_with_bot(
    sf: Any, *, slug: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed one tenant + one bot under it (committed). Returns (tenant, bot)."""
    tenant_id = uuid.uuid4()
    bot_pk = uuid.uuid4()
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, "
                "bypass_rate_limit, created_at, updated_at) VALUES "
                "(:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "name": f"rls-itest-{slug}"},
        )
        await session.execute(
            text(
                "INSERT INTO bots (id, bot_id, channel_type, workspace_id, "
                "record_tenant_id, bot_name, system_prompt, setting_options, "
                "custom_vocabulary, max_documents, plan_limits, "
                "bypass_token_limit, bypass_rate_limit, language, is_deleted, "
                "created_at, updated_at) VALUES "
                "(:id, :bot_id, 'web', :ws, :tid, 'rls-itest', '', "
                "'{}'::jsonb, '{}'::jsonb, 0, '{}'::jsonb, false, false, 'vi', "
                "false, now(), now())"
            ),
            {
                "id": bot_pk,
                "bot_id": f"rls-itest-{slug}",
                "ws": str(tenant_id),
                "tid": tenant_id,
            },
        )
        await session.commit()
    return tenant_id, bot_pk


async def _cleanup(sf: Any, tenant_ids: list[uuid.UUID]) -> None:
    async with sf() as session:
        for tid in tenant_ids:
            await session.execute(
                text("DELETE FROM bots WHERE record_tenant_id = :t"),
                {"t": tid},
            )
            await session.execute(
                text("DELETE FROM tenants WHERE id = :t"), {"t": tid},
            )
        await session.commit()


@pytest.mark.asyncio
async def test_guc_bound_to_a_returns_only_a(session_factory: Any) -> None:
    """GUC = tenant A → reads under the NOBYPASSRLS role see ONLY A's bot."""
    ta, _ = await _seed_tenant_with_bot(session_factory, slug="a")
    tb, _ = await _seed_tenant_with_bot(session_factory, slug="b")
    try:
        async with session_factory() as session:
            # SET LOCAL keeps the role + GUC bound to THIS transaction only.
            await session.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
            await session.execute(text(f"SET LOCAL app.tenant_id = '{ta}'"))
            rows = (
                await session.execute(
                    text("SELECT DISTINCT record_tenant_id FROM bots")
                )
            ).all()
            seen = {str(r[0]) for r in rows}
        assert str(ta) in seen, "tenant A's own bot must be visible"
        assert str(tb) not in seen, "tenant B's bot must NOT leak under GUC=A"
    finally:
        await _cleanup(session_factory, [ta, tb])


@pytest.mark.asyncio
async def test_no_guc_returns_zero_rows(session_factory: Any) -> None:
    """GUC never bound (fresh txn) → zero rows, fail-closed (no leak)."""
    ta, _ = await _seed_tenant_with_bot(session_factory, slug="noguc")
    try:
        async with session_factory() as session:
            await session.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
            # Deliberately do NOT set app.tenant_id.
            count = (
                await session.execute(text("SELECT count(*) FROM bots"))
            ).scalar_one()
        assert count == 0, "unbound GUC must fail-closed to zero rows"
    finally:
        await _cleanup(session_factory, [ta])


@pytest.mark.asyncio
async def test_document_service_index_unset_guc_does_not_throw(
    session_factory: Any,
) -> None:
    """RLS-2 regression: unset GUC on document_service_index → 0 rows, NO throw.

    Before the ``rls_missing_ok_setting`` migration the policy read
    ``current_setting('app.tenant_id')`` (no ``, true``), which RAISES
    ``UndefinedObjectError`` when the GUC is unset. The fix uses the
    missing_ok form so the query fail-closes to zero rows instead.
    """
    async with session_factory() as session:
        await session.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
        # No app.tenant_id set — this must NOT raise post-migration.
        try:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM document_service_index")
                )
            ).scalar_one()
        except DBAPIError as exc:  # pragma: no cover — only the pre-fix DB hits this
            pytest.fail(
                "document_service_index RLS policy still throws on unset GUC "
                f"(rls_missing_ok_setting migration not applied?): {exc}"
            )
        assert count == 0, "unbound GUC must fail-closed to zero rows"
