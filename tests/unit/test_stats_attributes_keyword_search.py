"""[T1-Smartness] B-FMA — ``query_by_name_keyword`` must search ``attributes_json``
for a long/specific keyword so a spec/SKU query reaches the PRICED row whose
spec lives in a non-name attribute cell (the alias flood) while ``entity_name``
holds a terse internal code. Gated by keyword length so a short generic token
("lốp") cannot match every row's attribute blob. Shape-only, domain-neutral.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from ragbot.infrastructure.repositories.stats_index_repository import (
    StatsIndexRepository,
)


class _CaptureSession:
    """Async-CM session double that records every SQL string executed."""

    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    async def __aenter__(self) -> "_CaptureSession":
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False

    async def execute(self, stmt: object, params: object = None) -> MagicMock:
        self._sink.append(str(stmt))
        r = MagicMock()
        r.fetchall.return_value = []
        r.first.return_value = None
        return r


def _repo(sink: list[str]) -> StatsIndexRepository:
    return StatsIndexRepository(session_factory=lambda: _CaptureSession(sink))


def test_long_spec_keyword_searches_attributes_json_in_where() -> None:
    sink: list[str] = []
    asyncio.run(
        _repo(sink).query_by_name_keyword(
            record_bot_id=uuid4(), keyword="155/80R13",
        )
    )
    sql = " ".join(sink)
    # The ::text cast appears ONLY in the WHERE attribute-search clause (the
    # SELECT lists plain ``attributes_json``), so it is a clean discriminator.
    assert "attributes_json::text" in sql, (
        "a long spec keyword must search attributes_json so the priced row is "
        "reachable (B-FMA)"
    )


def test_short_keyword_does_not_search_attributes_json() -> None:
    sink: list[str] = []
    asyncio.run(
        _repo(sink).query_by_name_keyword(
            record_bot_id=uuid4(), keyword="ab",
        )
    )
    sql = " ".join(sink)
    assert "attributes_json::text" not in sql, (
        "a short keyword must NOT over-match via attributes_json (length gate)"
    )
