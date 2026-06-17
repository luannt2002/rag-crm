"""CRIT-1 (F8 red-team report) — close cross-tenant audit-message leak.

``InvocationLogger.fetch_by_message_id`` previously filtered ONLY by
``message_id`` (BIGINT, guessable upstream id). Any tenant-admin (level
60+) of tenant A could iterate the BIGINT space and read tenant B's
full pipeline trace including chunk previews (PII), model names, costs.
Regression of P0-2.

Fix: ``record_tenant_id`` is now keyword-only and REQUIRED. Both inner
SELECTs filter by tenant. Tests pin the new contract so a future
refactor can't silently drop the kwarg.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from ragbot.infrastructure.observability.invocation_logger import (
    InvocationLogger,
)


class _FakeRow:
    """Stand-in for a SQLAlchemy ORM row with the columns we capture."""

    def __init__(
        self,
        *,
        request_id: UUID,
        record_tenant_id: UUID,
        message_id: int,
    ) -> None:
        # Mirror the column set used in `_row_to_dict` enough that the
        # repo's `req_ids = [r.request_id for r in logs]` line works.
        self.request_id = request_id
        self.record_tenant_id = record_tenant_id
        self.message_id = message_id

        # `_row_to_dict` walks ``row.__table__.columns``; expose a tiny
        # shim so the helper doesn't blow up on a fake row.
        class _Col:
            def __init__(self, name: str) -> None:
                self.name = name

        cols = [_Col("request_id"), _Col("record_tenant_id"), _Col("message_id")]
        self.__table__ = SimpleNamespace(columns=cols)


class _ScalarsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._rows)


class _RecordingSession:
    """Captures every executed SELECT + replays a programmable response.

    The response is keyed off the model's table name in the FROM clause
    so each call (request_logs / model_invocations / request_steps) can
    return a different fake list.
    """

    def __init__(self, registry: dict[str, list[Any]], log: list[Any]) -> None:
        self._registry = registry
        self._log = log

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> _ExecuteResult:
        compiled = stmt.compile(compile_kwargs={"literal_binds": False})
        sql = str(compiled)
        self._log.append(sql)
        for table_name, rows in self._registry.items():
            if table_name in sql:
                return _ExecuteResult(list(rows))
        return _ExecuteResult([])

    async def commit(self) -> None:
        return None


def _make_logger(
    registry: dict[str, list[Any]] | None = None,
) -> tuple[InvocationLogger, list[str]]:
    sql_log: list[str] = []
    reg = registry or {}

    def _factory() -> _RecordingSession:
        return _RecordingSession(reg, sql_log)

    return InvocationLogger(_factory), sql_log  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Signature — `record_tenant_id` is REQUIRED keyword-only.
# ---------------------------------------------------------------------------
def test_fetch_by_message_id_requires_tenant() -> None:
    """The kwarg is keyword-only AND has no default — calling without it
    must raise TypeError. Pins the CRIT-1 fix against future refactors
    that might re-introduce a `tenant_id=None` default."""
    sig = inspect.signature(InvocationLogger.fetch_by_message_id)
    params = sig.parameters
    assert "record_tenant_id" in params, "record_tenant_id parameter missing"
    p = params["record_tenant_id"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
        "record_tenant_id must be keyword-only"
    )
    assert p.default is inspect.Parameter.empty, (
        "record_tenant_id must NOT have a default — required at every call"
    )


@pytest.mark.asyncio
async def test_fetch_by_message_id_raises_when_tenant_missing() -> None:
    """Runtime confirmation — omitting the kwarg blows up before any
    DB call (no silent leak path)."""
    logger, _ = _make_logger()
    with pytest.raises(TypeError):
        await logger.fetch_by_message_id(123)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. Cross-tenant: tenant B asking for tenant A's message_id gets empty.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_by_message_id_returns_empty_for_other_tenant() -> None:
    """The fake registry has rows for tenant_a; querying as tenant_b
    must return the empty result because the SQL filter on
    ``record_tenant_id`` rejects them at the database layer.

    Test simulates this by short-circuiting any SELECT whose compiled
    SQL parameters bind tenant_b to ``record_tenant_id`` — we return
    an empty rowset, mirroring real Postgres behaviour.
    """
    tenant_a = uuid4()
    tenant_b = uuid4()

    # Smarter session that filters our fake rows by the bound tenant.
    class _SmartSession:
        def __init__(self, log: list[str]) -> None:
            self._log = log

        async def __aenter__(self) -> "_SmartSession":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def execute(self, stmt: Any) -> _ExecuteResult:
            compiled = stmt.compile(compile_kwargs={"literal_binds": False})
            sql = str(compiled)
            self._log.append(sql)
            params = compiled.params
            tid_param = params.get("record_tenant_id_1")
            # The query MUST bind a tenant — pin the contract.
            assert tid_param is not None, (
                f"SELECT executed without record_tenant_id filter: {sql}"
            )
            if str(tid_param) == str(tenant_b):
                return _ExecuteResult([])
            if "request_logs" in sql:
                return _ExecuteResult(
                    [
                        _FakeRow(
                            request_id=uuid4(),
                            record_tenant_id=tenant_a,
                            message_id=999,
                        )
                    ]
                )
            return _ExecuteResult([])

        async def commit(self) -> None:
            return None

    sql_log: list[str] = []

    def _factory() -> _SmartSession:
        return _SmartSession(sql_log)

    logger = InvocationLogger(_factory)  # type: ignore[arg-type]

    out = await logger.fetch_by_message_id(999, record_tenant_id=tenant_b)

    assert out == {
        "request_logs": [],
        "request_steps": [],
        "model_invocations": [],
    }, "tenant B must NOT see tenant A's audit data"
    # Sanity: every executed SQL filtered by record_tenant_id.
    assert sql_log, "no SQL was executed — test cannot certify isolation"
    for sql in sql_log:
        assert "record_tenant_id" in sql, (
            f"SELECT missing tenant filter — leak path open: {sql}"
        )


# ---------------------------------------------------------------------------
# 3. Same-tenant sanity — owning tenant gets their own data back.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_by_message_id_returns_data_for_owning_tenant() -> None:
    tenant_a = uuid4()
    req_id = uuid4()
    rows = [
        _FakeRow(request_id=req_id, record_tenant_id=tenant_a, message_id=42),
    ]
    registry = {
        "request_logs": rows,
        "model_invocations": [],
        "request_steps": [],
    }
    logger, _ = _make_logger(registry)

    out = await logger.fetch_by_message_id(42, record_tenant_id=tenant_a)
    assert len(out["request_logs"]) == 1
    assert out["request_logs"][0]["message_id"] == 42
    assert str(out["request_logs"][0]["record_tenant_id"]) == str(tenant_a)
