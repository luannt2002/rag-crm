"""[T1-Smartness] Pin tests — document_stats.py deterministic parser.

Tests verify:
- parse_money_vn: all VN money format variants + invalid/edge cases
- parse_table_chunks: CSV/table chunk parsing, header skip, multi-price
- aggregate_summary: bucket counts, empty input, categories
- alembic 0118: revision chain, RLS policy SQL present

HALLU=0 guarantee: every assertion checks a concrete deterministic value,
never an LLM output. The parser is pure Python regex.
"""
from __future__ import annotations

import importlib
import re

import pytest

from ragbot.shared.constants import DEFAULT_PRICE_BUCKETS_VND, DEFAULT_PRICE_MIN_VND
from ragbot.shared.document_stats import (
    ParsedEntity,
    aggregate_summary,
    parse_money_vn,
    parse_table_chunks,
)


# ===========================================================================
# parse_money_vn
# ===========================================================================


def test_parse_money_vn_dot_format() -> None:
    """Dotted-thousands: 1.499.000 → 1,499,000 VND."""
    assert parse_money_vn("1.499.000") == 1_499_000


def test_parse_money_vn_comma_format() -> None:
    """Comma-thousands: 1,499,000 → 1,499,000 VND."""
    assert parse_money_vn("1,499,000") == 1_499_000


def test_parse_money_vn_plain_format() -> None:
    """Plain integer: 1499000 → 1,499,000 VND."""
    assert parse_money_vn("1499000") == 1_499_000


def test_parse_money_vn_tr_format() -> None:
    """VN triệu shorthand: 1tr499 → 1,000,000 + 499,000 = 1,499,000."""
    assert parse_money_vn("1tr499") == 1_499_000


def test_parse_money_vn_tr_plain() -> None:
    """VN triệu with no remainder: 2tr → 2,000,000."""
    assert parse_money_vn("2tr") == 2_000_000


def test_parse_money_vn_tr_decimal() -> None:
    """Decimal triệu: 1.5tr → 1,500,000."""
    assert parse_money_vn("1.5tr") == 1_500_000


def test_parse_money_vn_k_format() -> None:
    """k-suffix shorthand: 499k → 499,000."""
    assert parse_money_vn("499k") == 499_000


def test_parse_money_vn_k_uppercase() -> None:
    """K-suffix case-insensitive: 499K → 499,000."""
    assert parse_money_vn("499K") == 499_000


def test_parse_money_vn_m_suffix() -> None:
    """M-suffix shorthand (English sheets): 1M → 1,000,000."""
    assert parse_money_vn("1M") == 1_000_000


def test_parse_money_vn_negative_returns_none() -> None:
    """Negative amounts are not valid prices → None."""
    # parse_money_vn only finds positive patterns; negatives have no match
    assert parse_money_vn("-500000") is None


def test_parse_money_vn_too_small_returns_none() -> None:
    """Values below DEFAULT_PRICE_MIN_VND are rejected (ordinal/code numbers)."""
    assert DEFAULT_PRICE_MIN_VND > 1_000  # sanity
    # 100 is below 10_000 threshold
    assert parse_money_vn("100") is None
    # 1234 is also below 10_000
    assert parse_money_vn("1234") is None


def test_parse_money_vn_no_pattern_returns_none() -> None:
    """Pure text with no numeric pattern → None."""
    assert parse_money_vn("liệt kê dịch vụ") is None


def test_parse_money_vn_embedded_in_text() -> None:
    """Money value embedded in a cell text is still extracted."""
    result = parse_money_vn("Giá: 1.499.000đ")
    assert result == 1_499_000


# ===========================================================================
# parse_table_chunks
# ===========================================================================


def _make_chunk(content: str, **extras: object) -> dict:
    return {"content": content, **extras}


def test_parse_table_chunks_basic_csv() -> None:
    """Basic CSV with header + 5 data rows → 5 ParsedEntity results."""
    content = (
        "STT,Tên,Giá\n"
        "1,Service A,499000\n"
        "2,Service B,1499000\n"
        "3,Service C,2499000\n"
        "4,Service D,3499000\n"
        "5,Service E,4999000\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 5
    names = [e.name for e in entities]
    assert "Service A" in names
    assert "Service E" in names
    # Prices parsed correctly
    by_name = {e.name: e for e in entities}
    assert by_name["Service A"].price_primary == 499_000
    assert by_name["Service B"].price_primary == 1_499_000


def test_parse_table_chunks_drops_noise_rows() -> None:
    """2026-06-20 stats-noise fix: prose/bullet/FAQ/name-less rows that merely
    contain a comma must NOT become catalog entities. They flooded the stats
    index ("- Giúp nâng cơ…", "Hiện tại dịch vụ…", name-less price cells) and
    polluted price/list retrieval (spa "dưới 500k" surfaced noise, not services).
    Real short-label services with prices must still survive.
    """
    content = (
        "STT,Tên dịch vụ,Giá\n"
        "1,Laser Carbon,1200000\n"               # real → keep
        "2,Nano kim cương,2500000\n"             # real (3 words) → keep
        "- Giúp nâng cơ, làm săn chắc da, đều màu sáng,199000\n"  # bullet desc → drop
        "Hiện tại dịch vụ chăm sóc da chuyên sâu tại spa được thực hiện chuẩn y khoa giúp làm sạch sâu,700000\n"  # long FAQ prose → drop
        ",,8000000\n"                            # name-less price cell → drop
    )
    entities = parse_table_chunks([_make_chunk(content)])
    names = {e.name for e in entities}
    assert "Laser Carbon" in names
    assert "Nano kim cương" in names
    # Noise must be gone:
    assert not any(n.lstrip().startswith("-") for n in names), f"bullet leaked: {names}"
    assert not any("chuẩn y khoa" in n for n in names), f"FAQ prose leaked: {names}"
    assert "" not in names, f"name-less row leaked: {names}"
    # Net: only the 2 real services survive.
    assert len(entities) == 2, f"expected 2 real entities, got {names}"


def test_parse_table_chunks_quoted_field_with_commas() -> None:
    """A quoted CSV cell containing commas stays ONE column (RFC-4180).

    Regression: a naive line.split(",") shattered a quoted synonym-list cell
    into N phantom columns, shifting every real column right so the header
    labels no longer aligned (quantity/price held code-variant garbage, the
    real numbers landed in col_N). csv.reader keeps the quoted cell intact so
    code / quantity / price / date land under their correct header keys.
    """
    content = (
        "question,code,productname,answer,quantity,price,date1,date2,image\n"
        '"195/65R15, 195 65 15, 195/65/15, 195-65-15",'
        "2-R15 195/65 LPD,"
        "Lop LANDSPIDER 195/65R15,"
        "LANDSPIDER 195/65R15 G/P,"
        "338,972000,26,,http://example/img\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    e = entities[0]
    # Real price lands in price_primary (was 1959... garbage before the fix).
    assert e.price_primary == 972_000
    # The labeled columns align with their header keys.
    assert e.attributes.get("code") == "2-R15 195/65 LPD"
    assert e.attributes.get("quantity") == "338"
    assert e.attributes.get("date1") == "26"
    assert e.attributes.get("answer") == "LANDSPIDER 195/65R15 G/P"
    # No phantom col_N keys from a shattered quoted cell.
    assert not any(k.startswith("col_") for k in e.attributes)


def test_parse_table_chunks_skip_non_data() -> None:
    """Prose/intro chunks without delimiters are skipped entirely."""
    prose = _make_chunk(
        "Đây là tài liệu giới thiệu dịch vụ. Không có bảng giá ở đây."
    )
    table = _make_chunk("Tên,Giá\nService A,499000\n")
    entities = parse_table_chunks([prose, table])
    # Only the table chunk contributes rows
    assert len(entities) == 1
    assert entities[0].name == "Service A"


def test_parse_table_chunks_multi_price_cols() -> None:
    """CSV with two price columns → price_primary + price_secondary."""
    content = (
        "Tên,Buổi lẻ,Combo\n"
        "Service X,499000,1499000\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.price_primary == 499_000
    assert entity.price_secondary == 1_499_000


def test_parse_table_chunks_pipe_delimited() -> None:
    """Markdown-style pipe-delimited table is parsed correctly."""
    content = (
        "| Tên | Giá |\n"
        "| --- | --- |\n"
        "| Service A | 499000 |\n"
        "| Service B | 1499000 |\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 2
    assert entities[0].price_primary == 499_000
    assert entities[1].price_primary == 1_499_000


def test_parse_table_chunks_category_from_heading() -> None:
    """Single-column non-price lines are treated as category headings."""
    content = (
        "Tên,Giá\n"
        "Mặt\n"                   # category heading (no price, no delimiter)
        "Service A,499000\n"
        "Service B,1499000\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    # Both rows under "Mặt" heading inherit the category
    for entity in entities:
        assert entity.category == "Mặt"


def test_parse_table_chunks_empty_chunk_list() -> None:
    """Empty input → empty output."""
    assert parse_table_chunks([]) == []


def test_parse_table_chunks_skips_header_and_separator() -> None:
    """Header row and separator row are not emitted as entities."""
    content = (
        "STT,Tên,Giá\n"
        "---,---,---\n"
        "1,Service A,499000\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    assert entities[0].name == "Service A"


# ===========================================================================
# aggregate_summary
# ===========================================================================


def _make_entity(price: int | None, category: str | None = None) -> ParsedEntity:
    return ParsedEntity(
        name="item",
        category=category,
        price_primary=price,
        price_secondary=None,
        chunk_index=0,
        attributes={},
    )


def test_aggregate_summary_buckets() -> None:
    """10 entities with known prices → correct bucket counts."""
    prices = [
        200_000,    # under 500k
        400_000,    # under 500k
        600_000,    # under 1M
        800_000,    # under 1M
        1_200_000,  # under 2M
        1_800_000,  # under 2M
        2_500_000,  # under 5M
        3_000_000,  # under 5M
        6_000_000,  # above 5M
        7_000_000,  # above 5M
    ]
    entities = [_make_entity(p) for p in prices]
    summary = aggregate_summary(entities)

    assert summary["entity_count"] == 10
    assert summary["price_primary_min"] == 200_000
    assert summary["price_primary_max"] == 7_000_000

    buckets = summary["price_buckets"]
    assert buckets["under_500k"] == 2
    assert buckets["under_1M"] == 2
    assert buckets["under_2M"] == 2
    assert buckets["under_5M"] == 2
    assert buckets["above_5M"] == 2


def test_aggregate_summary_empty() -> None:
    """0 entities → null price fields, 0 counts."""
    summary = aggregate_summary([])

    assert summary["entity_count"] == 0
    assert summary["price_primary_min"] is None
    assert summary["price_primary_max"] is None
    assert all(v == 0 for v in summary["price_buckets"].values())
    assert summary["categories"] == []


def test_aggregate_summary_categories_deduplicated() -> None:
    """Duplicate categories in entities → deduped sorted list."""
    entities = [
        _make_entity(499_000, category="Cat A"),
        _make_entity(599_000, category="Cat A"),
        _make_entity(699_000, category="Cat B"),
    ]
    summary = aggregate_summary(entities)
    assert summary["categories"] == ["Cat A", "Cat B"]


def test_aggregate_summary_none_prices_excluded() -> None:
    """Entities with price_primary=None are not counted in price stats."""
    entities = [
        _make_entity(None),
        _make_entity(500_000),
    ]
    summary = aggregate_summary(entities)
    assert summary["entity_count"] == 2
    assert summary["price_primary_min"] == 500_000
    assert summary["price_primary_max"] == 500_000
    # Only one entity has a price
    assert sum(summary["price_buckets"].values()) == 1


def test_aggregate_summary_bucket_keys_match_constants() -> None:
    """Bucket keys in summary are derived from DEFAULT_PRICE_BUCKETS_VND."""
    summary = aggregate_summary([_make_entity(1_000_000)])
    keys = list(summary["price_buckets"].keys())
    # Should have len(DEFAULT_PRICE_BUCKETS_VND) + 1 keys (last "above_X")
    assert len(keys) == len(DEFAULT_PRICE_BUCKETS_VND) + 1
    # First key starts with "under_"
    assert keys[0].startswith("under_")
    # Last key starts with "above_"
    assert keys[-1].startswith("above_")


# ===========================================================================
# Alembic 0118 structural checks (no live DB required)
# ===========================================================================


def test_alembic_0118_revision_chain() -> None:
    """Migration 0118 chains down_revision to 0117 (sequential chain post-renumber)."""
    import importlib.util
    import pathlib

    path = pathlib.Path(
        "alembic/_archive_pre_squash_20260618/20260526_0118_stats_index_schema.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0118", path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    assert mod.revision == "0118"
    assert mod.down_revision == "0117"


def test_alembic_0118_upgrade_sql_contains_create_table() -> None:
    """upgrade() SQL contains the document_service_index CREATE TABLE statement."""
    import inspect
    import pathlib

    src = pathlib.Path(
        "alembic/_archive_pre_squash_20260618/20260526_0118_stats_index_schema.py"
    ).read_text()

    assert "document_service_index" in src
    assert "CREATE TABLE IF NOT EXISTS" in src


def test_alembic_0118_rls_policy_enabled() -> None:
    """Migration SQL enables RLS and creates the tenant_isolation policy."""
    import pathlib

    src = pathlib.Path(
        "alembic/_archive_pre_squash_20260618/20260526_0118_stats_index_schema.py"
    ).read_text()

    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "tenant_isolation" in src
    assert "record_tenant_id = current_setting" in src


def test_alembic_0118_summary_json_column() -> None:
    """Migration includes ALTER TABLE documents ADD COLUMN summary_json."""
    import pathlib

    src = pathlib.Path(
        "alembic/_archive_pre_squash_20260618/20260526_0118_stats_index_schema.py"
    ).read_text()

    assert "summary_json" in src
    assert "JSONB" in src


def test_alembic_0118_downgrade_drops_table() -> None:
    """downgrade() SQL drops document_service_index and removes summary_json."""
    import pathlib

    src = pathlib.Path(
        "alembic/_archive_pre_squash_20260618/20260526_0118_stats_index_schema.py"
    ).read_text()

    assert "DROP TABLE IF EXISTS document_service_index" in src
    assert "DROP COLUMN IF EXISTS summary_json" in src
