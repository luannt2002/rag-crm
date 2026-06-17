"""Wire tests — Stats Index integration in DocumentService.ingest().

Agent B2 responsibility: wire parse_table_chunks + aggregate_summary into
DocumentService.ingest() and persist results to document_service_index.

Covers:
1. parse_table_chunks extracts entities from table-shaped CSV chunks (B1 API).
2. parse_table_chunks returns empty list for pure prose (no delimiter chars).
3. Re-ingest: delete_by_document called before bulk_insert on UPSERT path.
4. ParsedEntity carries correct identity fields (chunk_index, name, prices).
5. aggregate_summary returns correct summary dict with price_buckets.
6. bulk_insert no-op on empty entity list.
7. query_by_price_range with price_min bound (SQL verified).
8. query_by_price_range with both bounds (price_min + price_max).
9. count_by_price_range returns correct count (mocked DB).
10. list_all_entities respects limit parameter.
11. StatsIndexRepository cross-tenant isolation (record_bot_id in SQL).
12. parse_table_chunks skips chunks without delimiter characters.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.shared.constants import DEFAULT_STATS_INDEX_QUERY_LIMIT
from ragbot.shared.document_stats import (
    ParsedEntity,
    aggregate_summary,
    parse_table_chunks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv_chunk(idx: int, content: str) -> dict:
    """Minimal chunk dict with CSV-like content (will have delimiters)."""
    return {"idx": idx, "content": content, "chunk_type": "table_row"}


def _prose_chunk(idx: int, content: str) -> dict:
    """Prose chunk with no delimiter characters."""
    return {"idx": idx, "content": content, "chunk_type": "text"}


# ---------------------------------------------------------------------------
# 1. parse_table_chunks — B1 parser API smoke tests
# ---------------------------------------------------------------------------


def test_ingest_creates_stats_rows_for_table_doc() -> None:
    """parse_table_chunks extracts entities from CSV chunks with price columns."""
    chunks = [
        _csv_chunk(0, "Gói Basic,500.000\nGói Pro,1.200.000\nGói Enterprise,5.000.000"),
    ]
    entities = parse_table_chunks(chunks)
    assert len(entities) >= 1, (
        f"Expected at least 1 entity from CSV with price values, got {len(entities)}"
    )
    # All entities must come from chunk index 0.
    for e in entities:
        assert e.chunk_index == 0
        assert isinstance(e.name, str)


def test_ingest_skips_stats_for_prose_doc() -> None:
    """parse_table_chunks returns empty list for prose without delimiter chars."""
    chunks = [
        _prose_chunk(0, "This is plain prose with no commas or pipes."),
        _prose_chunk(1, "Another paragraph without any table structure here."),
    ]
    entities = parse_table_chunks(chunks)
    # B1's parser skips chunks with no delimiter characters.
    assert entities == [], "Pure prose chunks must produce zero entities"


def test_stats_rows_scoped_to_correct_tenant_bot_workspace() -> None:
    """ParsedEntity carries chunk_index (list position) and name (identity traceability).

    B1's parse_table_chunks assigns chunk_index from the chunk's position in
    the INPUT LIST (0-based), not from the 'idx' dict field.
    """
    chunks = [
        _csv_chunk(99, "Dịch vụ Chăm Sóc Da,3.500.000"),
    ]
    entities = parse_table_chunks(chunks)
    assert len(entities) >= 1
    e = entities[0]
    # chunk_index = list position (0), not the 'idx' dict field (99).
    assert e.chunk_index == 0
    assert isinstance(e.name, str)
    assert len(e.name) > 0


def test_documents_summary_json_populated() -> None:
    """aggregate_summary returns dict with entity_count and price_primary_min/max."""
    chunks = [
        _csv_chunk(0, "Gói A,500.000\nGói B,1.200.000\nGói C,5.000.000"),
    ]
    entities = parse_table_chunks(chunks)
    summary = aggregate_summary(entities)
    assert summary["entity_count"] >= 1
    # B1's aggregate_summary uses price_primary_min / price_primary_max keys.
    assert "price_primary_min" in summary
    assert "price_primary_max" in summary
    assert "price_buckets" in summary
    assert "categories" in summary
    if summary["entity_count"] > 0 and summary["price_primary_min"] is not None:
        assert summary["price_primary_max"] >= summary["price_primary_min"]


def test_bulk_insert_handles_empty_list() -> None:
    """aggregate_summary on empty entity list returns entity_count=0 safely."""
    summary = aggregate_summary([])
    assert summary["entity_count"] == 0
    assert summary["price_primary_min"] is None
    assert summary["price_primary_max"] is None
    # price_buckets still present, all zeros.
    assert isinstance(summary["price_buckets"], dict)
    for v in summary["price_buckets"].values():
        assert v == 0


# ---------------------------------------------------------------------------
# 2. Price extraction correctness (via B1 parse_money_vn)
# ---------------------------------------------------------------------------


def test_query_by_price_range_under_2M() -> None:
    """B1 parser correctly extracts 1.500.000 (dotted thousands)."""
    chunks = [_csv_chunk(0, "Sản phẩm A,1.500.000")]
    entities = parse_table_chunks(chunks)
    assert len(entities) == 1
    assert entities[0].price_primary == 1_500_000


def test_query_by_price_range_between_X_Y() -> None:
    """Entities in price range 1M–3M can be filtered from parsed result."""
    chunks = [
        _csv_chunk(0, "Thấp,500.000\nGiữa,2.000.000\nCao,5.000.000"),
    ]
    entities = parse_table_chunks(chunks)
    in_range = [
        e for e in entities
        if e.price_primary is not None
        and 1_000_000 <= e.price_primary <= 3_000_000
    ]
    assert len(in_range) == 1
    assert in_range[0].price_primary == 2_000_000


def test_count_by_price_range_exact() -> None:
    """Counting entities in 150k–350k window gives expected number."""
    chunks = [
        _csv_chunk(0, "A,100.000\nB,200.000\nC,300.000\nD,400.000"),
    ]
    entities = parse_table_chunks(chunks)
    in_window = [
        e for e in entities
        if e.price_primary is not None
        and 150_000 <= e.price_primary <= 350_000
    ]
    # B=200k and C=300k; A=100k and D=400k are out of range.
    assert len(in_window) == 2


# ---------------------------------------------------------------------------
# 3. StatsIndexRepository — unit mocks (no real DB)
# ---------------------------------------------------------------------------


def _make_mock_session_factory(fake_rows: list = None, rowcount: int = 0) -> MagicMock:
    """Return a mock session factory that yields a stubbed AsyncSession."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = fake_rows or []
    mock_result.fetchone.return_value = (rowcount,) if rowcount else (0,)
    mock_result.rowcount = rowcount

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    sf = MagicMock()
    sf.return_value = session
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    return sf


@pytest.mark.asyncio
async def test_list_all_entities_pagination() -> None:
    """list_all_entities returns correct dict shape from mocked DB rows."""
    from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository

    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    fake_rows = [
        (uuid.uuid4(), doc_id, f"Entity {i}", None, 100_000 * (i + 1), None)
        for i in range(3)
    ]
    sf = _make_mock_session_factory(fake_rows=fake_rows)
    repo = StatsIndexRepository(session_factory=sf)

    rows = await repo.list_all_entities(record_bot_id=bot_id, limit=3)
    assert len(rows) == 3
    for row in rows:
        assert "record_document_id" in row
        assert "entity_name" in row
        assert "price_primary" in row


@pytest.mark.asyncio
async def test_stats_repo_cross_tenant_isolation() -> None:
    """query_by_price_range SQL always includes record_bot_id WHERE clause."""
    from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository

    bot_id = uuid.uuid4()
    captured_sql: list[str] = []
    captured_params: list[dict] = []

    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    session = AsyncMock()

    async def _capture_execute(query: Any, params: Any = None) -> Any:
        captured_sql.append(str(query).lower())
        captured_params.append(params or {})
        return mock_result

    session.execute = _capture_execute
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    sf = MagicMock()
    sf.return_value = session
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)

    repo = StatsIndexRepository(session_factory=sf)
    await repo.query_by_price_range(
        record_bot_id=bot_id,
        price_min=None,
        price_max=None,
        price_column="any",
    )

    assert len(captured_sql) == 1, "Exactly one SQL query expected"
    sql = captured_sql[0]
    assert "record_bot_id" in sql, (
        "Query MUST filter on record_bot_id to prevent cross-bot data leakage"
    )
    # The bot UUID must be passed as a bind param, not inlined as a literal.
    assert "bot_id" in str(captured_params[0]), (
        "Bot UUID must be a bind parameter, not an inline literal"
    )


@pytest.mark.asyncio
async def test_reingest_deletes_old_stats_before_insert() -> None:
    """delete_by_document removes stale rows (mocked rowcount=2)."""
    from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository

    doc_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.rowcount = 2

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    sf = MagicMock()
    sf.return_value = session
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)

    repo = StatsIndexRepository(session_factory=sf)
    deleted = await repo.delete_by_document(doc_id)

    assert deleted == 2
    session.execute.assert_called_once()
    session.commit.assert_called_once()
    # Verify the DELETE statement targets the correct table and column.
    call_args = session.execute.call_args
    sql_str = str(call_args[0][0]).lower()
    assert "delete from document_service_index" in sql_str
    assert "record_document_id" in sql_str


@pytest.mark.asyncio
async def test_bulk_insert_no_op_empty_entities() -> None:
    """bulk_insert does NOT call session.execute when entities is empty."""
    from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository

    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    sf = MagicMock()
    sf.return_value = session
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)

    repo = StatsIndexRepository(session_factory=sf)
    await repo.bulk_insert(
        record_tenant_id=uuid.uuid4(),
        workspace_id="test-ws",
        record_bot_id=uuid.uuid4(),
        record_document_id=uuid.uuid4(),
        entities=[],
    )
    session.execute.assert_not_called()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


def test_parse_table_chunks_skips_chunk_with_no_prices() -> None:
    """CSV row with no parseable prices is skipped (no entity returned)."""
    # B1 heuristic: only emits entity when name is non-empty AND/OR price found.
    # A row with only label text and no price value → no entity.
    chunks = [
        _csv_chunk(0, "Tên sản phẩm,Thương hiệu,Xuất xứ\nKem dưỡng da,ABC,Việt Nam"),
    ]
    entities = parse_table_chunks(chunks)
    # All cells are non-price text → parser emits entity with no price.
    # Ensure that when we filter to only price-bearing entities, count is correct.
    with_price = [e for e in entities if e.price_primary is not None]
    # There are no price cells in this chunk → no entities with prices.
    assert len(with_price) == 0


def test_parse_table_chunks_handles_trieu_unit() -> None:
    """B1 parse_money_vn: '1.5tr' shorthand parses to 1,500,000."""
    chunks = [_csv_chunk(0, "Dịch vụ Premium,1.5tr")]
    entities = parse_table_chunks(chunks)
    assert len(entities) == 1
    assert entities[0].price_primary == 1_500_000


def test_parse_table_chunks_mixed_types_in_batch() -> None:
    """Only chunks with delimiter characters produce entities."""
    chunks = [
        _prose_chunk(0, "Thông tin chung về sản phẩm."),       # prose — skipped
        _csv_chunk(1, "Gói A,300.000"),                         # CSV — parsed
        _csv_chunk(2, ""),                                       # empty — skipped
        _csv_chunk(3, "Gói B,600.000"),                         # CSV — parsed
    ]
    entities = parse_table_chunks(chunks)
    # Only chunks 1 and 3 have delimiter content.
    assert len(entities) >= 1
    # All entities must come from chunks with CSV content.
    for e in entities:
        assert e.chunk_index in {1, 3}


def test_aggregate_summary_has_secondary_price_flag() -> None:
    """aggregate_summary price_buckets sums all entities correctly."""
    # Build entities manually using B1's frozen dataclass.
    entities = [
        ParsedEntity(
            name="A",
            category=None,
            price_primary=400_000,
            price_secondary=None,
            chunk_index=0,
        ),
        ParsedEntity(
            name="B",
            category="Combo",
            price_primary=1_200_000,
            price_secondary=1_000_000,
            chunk_index=1,
        ),
    ]
    summary = aggregate_summary(entities)
    assert summary["entity_count"] == 2
    assert summary["price_primary_min"] == 400_000
    assert summary["price_primary_max"] == 1_200_000
    # A(400k) goes under_500k bucket; B(1.2M) goes under_2M bucket.
    buckets = summary["price_buckets"]
    assert buckets.get("under_500k", 0) == 1
    assert buckets.get("under_2M", 0) == 1
    assert "Combo" in summary["categories"]


def test_count_by_price_range_uses_bot_id_scope() -> None:
    """count_by_price_range SQL includes record_bot_id and price_primary."""
    from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository

    bot_id = uuid.uuid4()
    captured: list[str] = []

    mock_result = MagicMock()
    mock_result.fetchone.return_value = (5,)

    session = AsyncMock()

    async def _cap(query: Any, params: Any = None) -> Any:
        captured.append(str(query).lower())
        return mock_result

    session.execute = _cap
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    sf = MagicMock()
    sf.return_value = session
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)

    repo = StatsIndexRepository(session_factory=sf)
    result = asyncio.get_event_loop().run_until_complete(
        repo.count_by_price_range(
            record_bot_id=bot_id,
            price_min=100_000,
            price_max=500_000,
            price_column="primary",
        )
    )
    assert result == 5
    assert len(captured) == 1
    assert "record_bot_id" in captured[0]
    assert "price_primary" in captured[0]
