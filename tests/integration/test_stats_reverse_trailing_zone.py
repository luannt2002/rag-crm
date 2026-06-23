"""Integration test — reverse-fallback trailing-zone match (M3).

A category-qualified price-of-entity query ("triệt lông mặt") carries the
category term FIRST and the granular zone name LAST. The structured index holds
the zone as its own short entity ("Mặt", 3 chars, priced) plus a category word
as a separate entity ("lông", 4 chars, null price). Before M3 the reverse
fallback (a) dropped the 3-char zone via the length floor and (b) a plain
CONTAINS match over-picked the longer mid-string category word — so the lookup
returned the NULL-price "lông" and the bot refused a service it actually prices.

M3: accept a short name when the keyword ENDS with it (trailing = the qualifying
zone) and order trailing + priced rows first. This asserts the real SQL against
Postgres: "triệt lông <zone>" returns the priced zone, not a mid-word category
entity. Hits the seeded dev corpus; skips when that data is absent (e.g. CI on a
bare test DB) so it never reports a false failure.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.repositories.stats_index_repository import (
    StatsIndexRepository,
)


def _database_url() -> str | None:
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
                if line.startswith("DATABASE_URL=") and "DATABASE_URL_SYNC" not in line:
                    return line.split("=", 1)[1].strip()
    return None


@pytest.mark.parametrize(
    "keyword,zone",
    [
        ("triệt lông mặt", "Mặt"),  # 3-char zone — the M3 regression
        ("triệt lông mép", "Mép"),  # 3-char zone
        ("triệt lông nách", "Nách"),  # 4-char zone — always worked, guards no-regression
    ],
)
def test_trailing_zone_returns_priced_zone(keyword: str, zone: str) -> None:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not available")

    async def _go() -> tuple[bool, list]:
        eng = create_async_engine(url)
        try:
            sf = async_sessionmaker(eng, expire_on_commit=False)
            async with eng.connect() as conn:
                bot_row = (
                    await conn.execute(
                        text(
                            "SELECT id, record_tenant_id FROM bots "
                            "WHERE bot_id = :b LIMIT 1"
                        ),
                        {"b": "test-spa-id"},
                    )
                ).first()
                if bot_row is None:
                    return (False, [])
                bid, tenant_id = bot_row[0], bot_row[1]
                # Require the seeded zone row to exist, else skip (bare CI DB).
                seeded = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM document_service_index "
                            "WHERE record_bot_id = :b AND entity_name = :z "
                            "AND (price_primary IS NOT NULL OR price_secondary IS NOT NULL) "
                            "LIMIT 1"
                        ),
                        {"b": bid, "z": zone},
                    )
                ).scalar()
                if seeded is None:
                    return (False, [])
            repo = StatsIndexRepository(session_factory=sf)
            rows = await repo.query_by_name_keyword(
                record_tenant_id=tenant_id,
                record_bot_id=bid,
                keyword=keyword,
                limit=5,
            )
            return (True, rows)
        finally:
            await eng.dispose()

    have_data, rows = asyncio.run(_go())
    if not have_data:
        pytest.skip("seeded spa stats data not present")

    assert rows, f"{keyword!r} returned no entity (reverse fallback dropped the zone)"
    top = rows[0]
    # The trailing zone, priced — NOT a null-price mid-word category entity.
    assert top["entity_name"] == zone, (
        f"{keyword!r} top result should be the trailing zone {zone!r}, "
        f"got {top['entity_name']!r} (mid-word over-match regression)"
    )
    assert (
        top["price_primary"] is not None or top["price_secondary"] is not None
    ), f"{keyword!r} returned the zone {zone!r} but with no price"
