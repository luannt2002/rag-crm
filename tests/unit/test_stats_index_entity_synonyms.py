"""Pin tests — entity_synonyms column wiring in StatsIndexRepository.

The Aliases role (document_stats) captures a ``;``-separated search-variant column
into ``ParsedEntity.aliases``. This pins that:
  - ``bulk_insert`` writes ``entity.aliases`` to the ``entity_synonyms`` column
    (bound param, NULL when no aliases).
  - ``query_by_name_keyword`` ORs an ``entity_synonyms`` match into each variant's
    clause (bound + fold), so an alias hits even when ``entity_name`` uses a
    different notation. Injection-safe — values live in params, not the SQL string.

Domain-neutral, deterministic. No real DB — a capture-fake session records SQL/params.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from ragbot.infrastructure.repositories.stats_index_repository import (
    StatsIndexRepository,
)
from ragbot.shared.document_stats import ParsedEntity


class _FakeResult:
    def fetchall(self):
        return []

    @property
    def rowcount(self):
        return 0


# ── bulk_insert writes entity_synonyms ──────────────────────────────────────
# bulk_insert opens its session via ``session_with_tenant(self._sf, ...)`` which
# calls ``factory()`` (NOT a context manager), runs SET-LOCAL execute()s, then the
# INSERT, then ``session.close()``. The fake below mirrors that contract and captures
# every execute() so the test can pick the INSERT (the one carrying ``VALUES``).


def _capture_factory(calls: list[tuple]):
    session = AsyncMock()

    async def _execute(stmt, params=None):
        calls.append((str(stmt), params or {}))
        return _FakeResult()

    session.execute = _execute
    session.commit = AsyncMock()
    session.close = AsyncMock()

    def _factory():
        return session

    return _factory


def _insert_call(calls: list[tuple]) -> tuple[str, dict]:
    for sql, params in calls:
        if "INSERT INTO document_service_index" in sql:
            return sql, params
    raise AssertionError(f"no INSERT captured among {[c[0][:40] for c in calls]}")


@pytest.mark.asyncio
async def test_bulk_insert_writes_entity_synonyms_column() -> None:
    calls: list[tuple] = []
    repo = StatsIndexRepository(session_factory=_capture_factory(calls))
    ent = ParsedEntity(
        name="Lốp A",
        category=None,
        price_primary=684_000,
        price_secondary=None,
        chunk_index=0,
        aliases="265/50R20; 265 50 R20",
    )
    await repo.bulk_insert(
        record_tenant_id=uuid.uuid4(),
        workspace_id="ws",
        record_bot_id=uuid.uuid4(),
        record_document_id=uuid.uuid4(),
        entities=[ent],
    )
    sql, params = _insert_call(calls)
    assert "entity_synonyms" in sql, "INSERT must target the entity_synonyms column"
    # The alias value is a BOUND param, never inlined.
    assert "265/50R20" not in sql
    assert any(
        v == "265/50R20; 265 50 R20" for v in params.values()
    ), f"alias must be a bound param: {params}"


@pytest.mark.asyncio
async def test_bulk_insert_entity_synonyms_null_when_no_aliases() -> None:
    calls: list[tuple] = []
    repo = StatsIndexRepository(session_factory=_capture_factory(calls))
    ent = ParsedEntity(
        name="Service Z",
        category=None,
        price_primary=499_000,
        price_secondary=None,
        chunk_index=0,
        aliases=None,
    )
    await repo.bulk_insert(
        record_tenant_id=uuid.uuid4(),
        workspace_id="ws",
        record_bot_id=uuid.uuid4(),
        record_document_id=uuid.uuid4(),
        entities=[ent],
    )
    _sql, params = _insert_call(calls)
    # The entity_synonyms bound value for this row must be None (SQL NULL).
    syn_params = [k for k in params if "synonym" in k.lower()]
    assert syn_params, f"expected a synonyms bound param: {list(params)}"
    assert all(params[k] is None for k in syn_params)


# ── query_by_name_keyword ORs entity_synonyms ───────────────────────────────
# query_by_name_keyword opens its session via ``self._sf()`` directly (a context
# manager) — a different contract from bulk_insert — so its fake yields a session.


class _QueryCaptureSession:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    async def execute(self, stmt, params=None):
        # Capture the FORWARD query (first call); the reverse fallback only runs on
        # an empty forward result, and we keep the first call as the synonym-expand
        # test does.
        if "sql" not in self._sink:
            self._sink["sql"] = str(stmt)
            self._sink["params"] = params or {}
        return _FakeResult()


def _query_sf(sink: dict):
    @asynccontextmanager
    async def _cm():
        yield _QueryCaptureSession(sink)

    return _cm


@pytest.mark.asyncio
async def test_query_by_name_keyword_matches_entity_synonyms() -> None:
    sink: dict = {}
    repo = StatsIndexRepository(session_factory=_query_sf(sink))
    await repo.query_by_name_keyword(
        record_bot_id=uuid.uuid4(),
        keyword="265/50R20",
    )
    sql = sink["sql"]
    params = sink["params"]
    # The forward match must consult entity_synonyms alongside entity_name/category.
    assert "entity_synonyms" in sql, "synonym column must be matched in the OR-clause"
    # Value still bound, never inlined.
    assert "265/50R20" not in sql
    assert params["kw0"] == "%265/50R20%"
