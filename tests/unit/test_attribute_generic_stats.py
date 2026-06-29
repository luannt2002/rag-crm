"""[T1-Smartness] F7 — the stats index is ATTRIBUTE-GENERIC, not price-centric.

The price_primary/price_secondary fields + NUMERIC columns + single-value-col model
re-introduced single-domain (price) coupling: only the first two money cells became
structured numeric data, and a non-price numeric column (stock count, area, quantity)
collapsed to a bare attribute value that no range query could reach.

F7 EVOLVES this additively: every numeric column persists as a LABELLED numeric
attribute inside the existing ``attributes_json`` JSONB under one reserved sub-key
(``DEFAULT_STATS_NUMERIC_ATTRS_KEY``). Price becomes ONE derived view of that map —
the price_primary/secondary fields and the price route stay byte-identical. The repo
gains an additive ``query_by_attribute_range`` that range-filters the labelled map.

These tests pin BOTH directions:
  * a NON-price numeric column is captured as a labelled numeric attribute and is
    range-queryable by its corpus header (bound params, JSONB cast, tenant scope,
    live-doc join);
  * existing price behaviour (price_primary/secondary + the price under its header)
    is unchanged, and an unpriced row's attributes_json carries NO reserved key.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from ragbot.infrastructure.repositories.stats_index_repository import (
    StatsIndexRepository,
)
from ragbot.shared.constants import DEFAULT_STATS_NUMERIC_ATTRS_KEY
from ragbot.shared.document_stats import parse_table_chunks


def _chunk(body: str) -> dict:
    return {"content": body, "chunk_index": 0}


# ── WRITE SIDE: a non-price numeric column is a labelled numeric attribute ──────
def test_non_price_numeric_column_captured_as_labelled_attribute() -> None:
    # Owner declares a stock column as a generic ``attribute`` so it is NOT a price.
    # It must still be recorded as a labelled NUMERIC attribute so it is queryable.
    chunk = _chunk("Tên,Giá,Tồn kho\nSản phẩm A,1200000,40400\n")
    entities = parse_table_chunks(
        [chunk], custom_roles={"Tên": "name", "Giá": "price", "Tồn kho": "attribute"}
    )
    assert len(entities) == 1
    ent = entities[0]
    # Price still binds to the dedicated field (derived view unchanged).
    assert ent.price_primary == 1200000
    # The stock count is NOT a price (owner-declared attribute), yet it IS a
    # labelled numeric attribute under the reserved key.
    numeric = ent.attributes[DEFAULT_STATS_NUMERIC_ATTRS_KEY]
    assert numeric["Tồn kho"] == 40400
    # Price is one entry in the SAME labelled-numeric map (one derived view).
    assert numeric["Giá"] == 1200000
    # The string display value is also kept (render byte-identical for the LLM).
    assert ent.attributes["Tồn kho"] == "40400"


def test_inferred_second_numeric_column_is_labelled() -> None:
    # Pure inference (no custom_roles): a 2nd price-shaped column lands in the
    # numeric map under its header so it is range-queryable, not just price_secondary.
    chunk = _chunk("Tên,Đơn giá,Giá combo\nGói A,500000,1200000\n")
    ent = parse_table_chunks([chunk])[0]
    assert ent.price_primary == 500000
    assert ent.price_secondary == 1200000
    numeric = ent.attributes[DEFAULT_STATS_NUMERIC_ATTRS_KEY]
    assert numeric["Đơn giá"] == 500000
    assert numeric["Giá combo"] == 1200000


def test_unpriced_row_has_no_reserved_numeric_key() -> None:
    # A row with no numeric column keeps a byte-identical attributes_json: the
    # reserved key is written ONLY when the numeric map is non-empty.
    chunk = _chunk("Tên,Ghi chú\nSản phẩm A,màu đỏ\n")
    ent = parse_table_chunks([chunk])[0]
    assert ent.price_primary is None
    assert DEFAULT_STATS_NUMERIC_ATTRS_KEY not in ent.attributes


def test_price_only_happy_path_byte_identical_attributes() -> None:
    # The default VN happy path (name + price) is unchanged: price under its header
    # plus the additive numeric map; no other attribute is invented.
    chunk = _chunk("Tên,Giá\nDịch vụ A,500000\n")
    ent = parse_table_chunks([chunk])[0]
    assert ent.price_primary == 500000
    # Headerful price surfaced under its corpus header (pre-F7 behaviour kept).
    assert ent.attributes["Giá"] == 500000
    # Reserved numeric map present and contains exactly the price (no extra keys).
    assert ent.attributes[DEFAULT_STATS_NUMERIC_ATTRS_KEY] == {"Giá": 500000}


# ── READ SIDE: query_by_attribute_range over the labelled JSONB numeric map ─────
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, sink, rows):
        self._sink = sink
        self._rows = rows

    async def execute(self, stmt, params):
        self._sink["sql"] = str(stmt)
        self._sink["params"] = params
        return _FakeResult(self._rows)


def _fake_sf(sink, rows):
    @asynccontextmanager
    async def _cm():
        yield _FakeSession(sink, rows)

    return _cm


@pytest.mark.asyncio
async def test_query_by_attribute_range_builds_scoped_jsonb_range_sql() -> None:
    sink: dict = {}
    bot_id = uuid.uuid4()
    # row shape mirrors the other renderer-feeding queries (selects attributes_json)
    row = (
        uuid.uuid4(), uuid.uuid4(), None, "Sản phẩm A", None, 1200000, None,
        {DEFAULT_STATS_NUMERIC_ATTRS_KEY: {"Tồn kho": 40400}},
    )
    repo = StatsIndexRepository(session_factory=_fake_sf(sink, [row]))
    out = await repo.query_by_attribute_range(
        record_bot_id=bot_id, label="Tồn kho", value_min=100, value_max=None,
    )
    sql = sink["sql"]
    params = sink["params"]
    # tenant/bot scope + live-doc join (no cross-tenant / deleted-doc leak)
    assert "dsi.record_bot_id = :bot_id" in sql
    assert "d.deleted_at IS NULL" in sql
    assert params["bot_id"] == bot_id
    # the label is a BOUND param into the JSONB path, never interpolated (injection-safe)
    assert params["attr_label"] == "Tồn kho"
    assert "Tồn kho" not in sql
    # the JSONB numeric sub-map is range-filtered as a NUMERIC, not a string
    assert DEFAULT_STATS_NUMERIC_ATTRS_KEY in sql
    assert "numeric" in sql.lower()
    assert params["value_min"] == 100
    # selects attributes_json so the synthetic-chunk renderer keeps working
    assert "attributes_json" in sql
    # row mapped back with its labelled numeric map intact
    assert out[0]["attributes_json"][DEFAULT_STATS_NUMERIC_ATTRS_KEY]["Tồn kho"] == 40400


@pytest.mark.asyncio
async def test_query_by_attribute_range_max_only_bound() -> None:
    sink: dict = {}
    repo = StatsIndexRepository(session_factory=_fake_sf(sink, []))
    await repo.query_by_attribute_range(
        record_bot_id=uuid.uuid4(), label="Diện tích", value_min=None, value_max=50,
    )
    params = sink["params"]
    assert params["value_max"] == 50
    assert "value_min" not in params  # no lower bound supplied → no min clause
    assert params["attr_label"] == "Diện tích"


@pytest.mark.asyncio
async def test_query_by_attribute_range_blank_label_returns_empty() -> None:
    # A blank label can't address a sub-map key → return [] without touching the DB.
    sink: dict = {}
    repo = StatsIndexRepository(session_factory=_fake_sf(sink, []))
    out = await repo.query_by_attribute_range(
        record_bot_id=uuid.uuid4(), label="  ", value_min=1, value_max=2,
    )
    assert out == []
    assert "sql" not in sink  # short-circuited before any query
