"""PgBM25Retrieval adapter unit tests.

Coverage:
- Returns chunks with the canonical DTO shape (chunk_id, score, ...) from
  a mocked session_factory (no live DB needed).
- Empty / whitespace query short-circuits to []  (no SQL executed).
- Missing record_bot_id short-circuits to [] (tenant isolation guard).
- DB driver exception is swallowed (auxiliary signal must not crash retrieve).
- ValueError / TypeError swallowed (narrow whitelist path).
- Health check is True under a working factory.
- Health check is False when the factory raises.
- get_provider_name() == "pg_textsearch" (drift guard).
- Bind params include the tokenized query + record_bot_id (tenant scope).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from ragbot.infrastructure.retrieval.pg_bm25_retrieval import PgBM25Retrieval


# ----- Shared async-session fake (mirrors test_retrieval_stages pattern) ---


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self):
        class _M:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _M(self._rows)


class _FakeSession:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._rows = rows or []
        self._raise = raise_exc
        self.last_sql: str | None = None
        self.last_params: dict | None = None

    async def execute(self, statement, params=None):  # noqa: ANN001
        self.last_sql = str(statement)
        self.last_params = params
        if self._raise:
            raise self._raise
        return _FakeResult(self._rows)


def _make_session_factory(
    rows: list[dict[str, Any]] | None = None,
    raise_exc: Exception | None = None,
    sink: list | None = None,
):
    @asynccontextmanager
    async def _cm():
        s = _FakeSession(rows=rows, raise_exc=raise_exc)
        if sink is not None:
            sink.append(s)
        try:
            yield s
        finally:
            pass

    def _factory():
        return _cm()

    return _factory


# ----- Tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_canonical_chunk_dicts() -> None:
    rows = [
        {
            "id": "chunk-A",
            "record_document_id": "doc-1",
            "chunk_index": 0,
            "content": "literal exact text match",
            "metadata_json": {"src": "doc1.pdf"},
            "score": 0.91,
        },
        {
            "id": "chunk-B",
            "record_document_id": "doc-1",
            "chunk_index": 3,
            "content": "another snippet",
            "metadata_json": None,
            "score": 0.42,
        },
    ]
    sf = _make_session_factory(rows=rows)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("literal exact text", uuid4(), top_k=10)
    assert len(out) == 2
    # Canonical DTO shape — matches the vector branch so RRF merge dedupes.
    assert out[0]["chunk_id"] == "chunk-A"
    assert out[0]["document_id"] == "doc-1"
    assert out[0]["content"] == "literal exact text match"
    assert out[0]["text"] == out[0]["content"]
    assert out[0]["score"] == pytest.approx(0.91)
    assert out[0]["source"] == "lexical"
    # Metadata null → empty dict (downstream never sees None).
    assert out[1]["metadata"] == {}


@pytest.mark.asyncio
async def test_empty_query_returns_empty_no_sql() -> None:
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[{"id": "x"}], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("   ", uuid4(), top_k=10)
    assert out == []
    # Short-circuit before opening a session.
    assert sink == []


@pytest.mark.asyncio
async def test_missing_record_bot_id_returns_empty() -> None:
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[{"id": "x"}], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("any query", None, top_k=10)  # type: ignore[arg-type]
    assert out == []
    # Tenant guard short-circuits before opening a session.
    assert sink == []


@pytest.mark.asyncio
async def test_db_driver_exception_swallowed_returns_empty() -> None:
    # Aux signal must NEVER crash retrieve — simulate a driver-specific
    # exception (RuntimeError stands in for asyncpg/psycopg subclasses).
    sf = _make_session_factory(raise_exc=RuntimeError("connection reset"))
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("query", uuid4(), top_k=10)
    assert out == []


@pytest.mark.asyncio
async def test_narrow_exception_swallowed_returns_empty() -> None:
    sf = _make_session_factory(raise_exc=ValueError("bad bind param"))
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("query", uuid4(), top_k=10)
    assert out == []


@pytest.mark.asyncio
async def test_health_check_passes_when_session_works() -> None:
    sf = _make_session_factory(rows=[])
    adapter = PgBM25Retrieval(session_factory=sf)
    assert (await adapter.health_check()) is True


@pytest.mark.asyncio
async def test_health_check_fails_when_factory_raises() -> None:
    sf = _make_session_factory(raise_exc=RuntimeError("db down"))
    adapter = PgBM25Retrieval(session_factory=sf)
    assert (await adapter.health_check()) is False


def test_provider_name_constant() -> None:
    # Drift guard: registry key + adapter name must agree.
    assert PgBM25Retrieval.get_provider_name() == "pg_textsearch"


@pytest.mark.asyncio
async def test_tenant_isolation_record_bot_id_in_bind_params() -> None:
    # Sanity: the record_bot_id bind is the only tenant key the adapter
    # has — verify it actually reaches the SQL session.
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    rbid = uuid4()
    await adapter.search("any query", rbid, top_k=10)
    assert len(sink) == 1
    assert sink[0].last_params is not None
    assert sink[0].last_params["rbid"] == rbid
    assert sink[0].last_params["query"] == "any query"
    assert sink[0].last_params["top_k"] == 10


@pytest.mark.asyncio
async def test_score_none_coerced_to_zero() -> None:
    # ts_rank_cd can theoretically return NULL for degenerate docs; guard
    # the float() coercion so the orchestrator never sees NaN/None.
    rows = [
        {
            "id": "c1",
            "record_document_id": "d1",
            "chunk_index": 0,
            "content": "x",
            "metadata_json": {},
            "score": None,
        },
    ]
    sf = _make_session_factory(rows=rows)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("q", uuid4(), top_k=5)
    assert out[0]["score"] == 0.0


@pytest.mark.asyncio
async def test_normalization_flags_clamped() -> None:
    # Out-of-range bitmask → clamped to [0, 63] so SQL stays safe.
    a1 = PgBM25Retrieval(session_factory=_make_session_factory(), normalization_flags=999)
    a2 = PgBM25Retrieval(session_factory=_make_session_factory(), normalization_flags=-7)
    assert 0 <= a1._norm <= 63  # noqa: SLF001 — bounds-check is the test's whole point
    assert 0 <= a2._norm <= 63  # noqa: SLF001
