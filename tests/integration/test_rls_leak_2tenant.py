"""RLS leak test — 2 tenants + 2 workspaces, NOT green-vacuous (ADR-W1-D3).

The charter AN TOÀN gate: "RLS leak test 2-tenant pass trong CI · 0
cross-tenant row". P2-C proved the previous state was green-but-meaningless:
the app connected as ``postgres`` (rolsuper + rolbypassrls), so any
row-count assertion passed without RLS ever engaging.

This suite is built to be IMPOSSIBLE to pass vacuously:

1. **Role guard** — the connected role must be non-superuser AND
   non-BYPASSRLS, otherwise the test FAILS (not skips): a leak test that
   ran as a bypass role is itself a defect.
2. Cross-tenant isolation — tenant A's GUC must see 0 of tenant B's rows.
3. Workspace isolation — within one tenant, workspace W1's GUC must see 0
   of W2's bots (0141 policy clause, supplied by ``app.workspace_id``).
4. Negative control — the same query under the superuser DSN sees BOTH
   tenants, proving the assertions are sensitive to the role.

Requires ``DATABASE_URL_APP`` (the ``ragbot_app`` NOBYPASSRLS DSN); the
whole module skips with a loud reason when it is absent so CI surfaces
"AN TOÀN gate not yet armed" instead of a silent green.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("DATABASE_URL_APP"),
        reason=(
            "DATABASE_URL_APP (ragbot_app NOBYPASSRLS DSN) not set — "
            "RLS leak gate NOT ARMED (ops step, ADR-W1-D3 piece 3)"
        ),
    ),
]

_TENANT_A = uuid.uuid4()
_TENANT_B = uuid.uuid4()
_WS_1 = "leaktest-ws-one"
_WS_2 = "leaktest-ws-two"


def _connect_kwargs(dsn: str) -> str:
    return dsn.replace("+asyncpg", "").replace("postgresql+psycopg", "postgresql")


async def _fetch_one(conn, sql: str, *args):
    return await conn.fetchval(sql, *args)


@pytest.fixture()
async def app_conn():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(_connect_kwargs(os.environ["DATABASE_URL_APP"]))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture()
async def super_conn():
    asyncpg = pytest.importorskip("asyncpg")
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set for negative control")
    conn = await asyncpg.connect(_connect_kwargs(dsn))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture()
async def seeded(super_conn):
    """Seed 2 tenants × bots (tenant A carries 2 workspaces). Superuser
    seeds; app role reads. Cleanup is unconditional."""
    bot_rows = [
        (uuid.uuid4(), _TENANT_A, _WS_1, "leaktest-bot-a1"),
        (uuid.uuid4(), _TENANT_A, _WS_2, "leaktest-bot-a2"),
        (uuid.uuid4(), _TENANT_B, str(_TENANT_B), "leaktest-bot-b1"),
    ]
    await super_conn.execute(
        "INSERT INTO tenants (id, name) VALUES ($1, $2), ($3, $4) "
        "ON CONFLICT (id) DO NOTHING",
        _TENANT_A, "leaktest-tenant-a", _TENANT_B, "leaktest-tenant-b",
    )
    for bot_uuid, tenant, ws, slug in bot_rows:
        await super_conn.execute(
            "INSERT INTO bots (id, record_tenant_id, workspace_id, bot_id, "
            "channel_type, name) VALUES ($1, $2, $3, $4, 'web', $4) "
            "ON CONFLICT DO NOTHING",
            bot_uuid, tenant, ws, slug,
        )
    try:
        yield
    finally:
        await super_conn.execute(
            "DELETE FROM bots WHERE bot_id LIKE 'leaktest-bot-%'",
        )
        await super_conn.execute(
            "DELETE FROM tenants WHERE id = ANY($1::uuid[])",
            [_TENANT_A, _TENANT_B],
        )


async def test_role_guard_connection_is_not_bypass(app_conn):
    """The crucial guard: a leak test running as a bypass role is a FAIL."""
    row = await app_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
    )
    assert row is not None
    assert not row["rolsuper"] and not row["rolbypassrls"], (
        f"leak-test connected as bypass role (super={row['rolsuper']} "
        f"bypassrls={row['rolbypassrls']}) — RLS never engaged; "
        "the test would be green-vacuous. Point DATABASE_URL_APP at "
        "the ragbot_app NOBYPASSRLS role."
    )


async def test_cross_tenant_rows_invisible(app_conn, seeded):
    async with app_conn.transaction():
        await app_conn.execute(f"SET LOCAL app.tenant_id = '{_TENANT_A}'")
        leak = await _fetch_one(
            app_conn,
            "SELECT count(*) FROM bots WHERE record_tenant_id = $1",
            _TENANT_B,
        )
        own = await _fetch_one(
            app_conn,
            "SELECT count(*) FROM bots WHERE bot_id LIKE 'leaktest-bot-%'",
        )
    assert leak == 0, f"tenant A sees {leak} of tenant B's rows — RLS LEAK"
    assert own == 2, f"tenant A must see exactly its own 2 seeded bots, got {own}"


async def test_workspace_rows_invisible_within_tenant(app_conn, seeded):
    async with app_conn.transaction():
        await app_conn.execute(f"SET LOCAL app.tenant_id = '{_TENANT_A}'")
        await app_conn.execute(f"SET LOCAL app.workspace_id = '{_WS_1}'")
        leak = await _fetch_one(
            app_conn,
            "SELECT count(*) FROM bots WHERE workspace_id = $1",
            _WS_2,
        )
    assert leak == 0, (
        f"workspace {_WS_1} sees {leak} rows of {_WS_2} — the 0141 "
        "workspace clause is not engaging (app.workspace_id GUC dead?)"
    )


async def test_negative_control_superuser_sees_both(super_conn, seeded):
    """Sensitivity proof: WITHOUT the app role the same query sees both
    tenants. If this fails, the seed/fixture is broken — and a green
    cross-tenant test above would be meaningless."""
    total = await _fetch_one(
        super_conn,
        "SELECT count(*) FROM bots WHERE bot_id LIKE 'leaktest-bot-%'",
    )
    assert total == 3, f"negative control expects all 3 seeded bots, got {total}"
