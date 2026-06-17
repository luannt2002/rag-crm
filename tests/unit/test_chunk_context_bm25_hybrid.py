"""CT-3 — chunk_context wired into BM25 hybrid retrieval.

Coverage:
1. ``cr_enhanced=False`` (default) keeps the legacy SQL bit-exact —
   uses ``dc.search_vector`` column, NEVER references ``chunk_context``.
2. ``cr_enhanced=True`` switches the SQL to a functional tsvector that
   tokenizes ``content || ' ' || coalesce(chunk_context, '')``.
3. NULL ``chunk_context`` rows fall back gracefully via ``coalesce`` —
   the SQL must include ``coalesce(.*chunk_context`` not bare reference.
4. Tenant isolation is preserved on both paths — ``record_bot_id``
   binds the WHERE clause whether cr_enhanced flips or not.
5. Empty / whitespace query short-circuits on the cr_enhanced path
   (same guard as legacy).
6. Missing ``record_bot_id`` short-circuits on the cr_enhanced path.
7. Top-K + bind params are wired correctly on the cr_enhanced path.
8. Driver exceptions are swallowed on both paths (aux signal must
   not crash retrieve).
9. ``NullLexicalRetrieval`` accepts ``cr_enhanced=`` kwarg without
   error and still returns ``[]`` (Null Object contract).
10. ``Port`` Protocol accepts the ``cr_enhanced`` kwarg (compile-time
    check via duck-typed call against the runtime Protocol).
11. Default-arg backward compat — adapter callable with positional
    3-arg signature (no cr_enhanced) keeps working.
12. SQL guarantee — cr_enhanced=True path must NOT reference the
    legacy ``dc.search_vector`` column (else the functional GIN index
    won't be picked).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from ragbot.infrastructure.retrieval.null_lexical_retrieval import NullLexicalRetrieval
from ragbot.infrastructure.retrieval.pg_bm25_retrieval import PgBM25Retrieval


# ----- Shared async-session fake (mirrors test_pg_bm25_retrieval pattern) -


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self):  # noqa: ANN201
        class _M:
            def __init__(self, rows):  # noqa: ANN001
                self._rows = rows

            def all(self):  # noqa: ANN201
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

    async def execute(self, statement, params=None):  # noqa: ANN001, ANN201
        self.last_sql = str(statement)
        self.last_params = params
        if self._raise:
            raise self._raise
        return _FakeResult(self._rows)


def _make_session_factory(
    rows: list[dict[str, Any]] | None = None,
    raise_exc: Exception | None = None,
    sink: list | None = None,
):  # noqa: ANN202
    @asynccontextmanager
    async def _cm():  # noqa: ANN202
        s = _FakeSession(rows=rows, raise_exc=raise_exc)
        if sink is not None:
            sink.append(s)
        try:
            yield s
        finally:
            pass

    def _factory():  # noqa: ANN202
        return _cm()

    return _factory


# ----- Tests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_cr_disabled_uses_search_vector_column_only() -> None:
    """Default (cr_enhanced=False) MUST keep the legacy indexed path."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    await adapter.search("query text", uuid4(), top_k=10)
    assert len(sink) == 1
    sql = sink[0].last_sql or ""
    # Legacy path uses the trigger-maintained column.
    assert "dc.search_vector" in sql
    # And MUST NOT widen to chunk_context — bit-exact pre-CR behaviour.
    assert "chunk_context" not in sql


@pytest.mark.asyncio
async def test_cr_enabled_widens_tsvector_to_content_plus_context() -> None:
    """Opt-in path tokenizes content + chunk_context on the fly."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    await adapter.search("query text", uuid4(), top_k=10, cr_enhanced=True)
    assert len(sink) == 1
    sql = sink[0].last_sql or ""
    # Functional tsvector references both columns.
    assert "to_tsvector" in sql
    assert "dc.content" in sql
    assert "dc.chunk_context" in sql


@pytest.mark.asyncio
async def test_cr_enabled_uses_coalesce_for_null_chunk_context() -> None:
    """NULL chunk_context must coalesce to '' so retrieval is graceful."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    await adapter.search("q", uuid4(), top_k=5, cr_enhanced=True)
    sql = sink[0].last_sql or ""
    # Both columns wrapped in coalesce — no bare references that would
    # NULL-out the whole tsvector for unenriched legacy rows.
    assert "coalesce(dc.content" in sql
    assert "coalesce(dc.chunk_context" in sql


@pytest.mark.asyncio
async def test_cr_enabled_preserves_tenant_isolation_join() -> None:
    """record_bot_id bind + documents JOIN must survive the widened path."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    rbid = uuid4()
    await adapter.search("q", rbid, top_k=5, cr_enhanced=True)
    sql = sink[0].last_sql or ""
    assert "JOIN documents d" in sql
    assert "d.record_bot_id = :rbid" in sql
    assert "d.deleted_at IS NULL" in sql
    assert sink[0].last_params["rbid"] == rbid


@pytest.mark.asyncio
async def test_cr_enabled_empty_query_short_circuits() -> None:
    """Whitespace query never opens a session — same as legacy."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[{"id": "x"}], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("  ", uuid4(), top_k=10, cr_enhanced=True)
    assert out == []
    assert sink == []


@pytest.mark.asyncio
async def test_cr_enabled_missing_record_bot_id_short_circuits() -> None:
    """Missing tenant key guards the cr_enhanced path too."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[{"id": "x"}], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search(
        "q", None, top_k=10, cr_enhanced=True,  # type: ignore[arg-type]
    )
    assert out == []
    assert sink == []


@pytest.mark.asyncio
async def test_cr_enabled_top_k_and_bind_params_wired() -> None:
    """top_k + query bind reach the SQL exec on the cr_enhanced path."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    rbid = uuid4()
    await adapter.search("specific query", rbid, top_k=7, cr_enhanced=True)
    assert sink[0].last_params["query"] == "specific query"
    assert sink[0].last_params["top_k"] == 7
    assert sink[0].last_params["rbid"] == rbid


@pytest.mark.asyncio
async def test_cr_enabled_db_exception_swallowed() -> None:
    """Driver exception on the cr_enhanced path must NOT crash retrieve."""
    sf = _make_session_factory(raise_exc=RuntimeError("plan error"))
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("q", uuid4(), top_k=5, cr_enhanced=True)
    assert out == []


@pytest.mark.asyncio
async def test_cr_enabled_returns_canonical_dto_shape() -> None:
    """Output shape must match the legacy path so RRF merge dedupes."""
    rows = [
        {
            "id": "chunk-Z",
            "record_document_id": "doc-9",
            "chunk_index": 2,
            "content": "tokenized body text",
            "metadata_json": {"k": "v"},
            "score": 0.77,
        },
    ]
    sf = _make_session_factory(rows=rows)
    adapter = PgBM25Retrieval(session_factory=sf)
    out = await adapter.search("q", uuid4(), top_k=5, cr_enhanced=True)
    assert len(out) == 1
    assert out[0]["chunk_id"] == "chunk-Z"
    assert out[0]["document_id"] == "doc-9"
    assert out[0]["content"] == "tokenized body text"
    assert out[0]["source"] == "lexical"
    assert out[0]["score"] == pytest.approx(0.77)


@pytest.mark.asyncio
async def test_null_lexical_accepts_cr_enhanced_kwarg() -> None:
    """Null Object MUST accept the new kwarg without raising."""
    null = NullLexicalRetrieval()
    # Both call forms — positional baseline + new kwarg.
    assert await null.search("q", uuid4(), 10) == []
    assert await null.search("q", uuid4(), 10, cr_enhanced=True) == []
    assert await null.search("q", uuid4(), 10, cr_enhanced=False) == []


@pytest.mark.asyncio
async def test_positional_call_still_works_backward_compat() -> None:
    """3-arg positional call (legacy) must keep working — default False."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    # No cr_enhanced kwarg — legacy callers stay byte-identical.
    await adapter.search("q", uuid4(), 5)
    sql = sink[0].last_sql or ""
    assert "dc.search_vector" in sql
    assert "chunk_context" not in sql


@pytest.mark.asyncio
async def test_cr_enabled_drops_search_vector_column_reference() -> None:
    """cr_enhanced=True path MUST NOT reference dc.search_vector — else
    Postgres can't pick the functional GIN index (alembic 010n)."""
    sink: list[_FakeSession] = []
    sf = _make_session_factory(rows=[], sink=sink)
    adapter = PgBM25Retrieval(session_factory=sf)
    await adapter.search("q", uuid4(), top_k=5, cr_enhanced=True)
    sql = sink[0].last_sql or ""
    # The trigger-maintained column is unused on the opt-in path.
    assert "dc.search_vector" not in sql
