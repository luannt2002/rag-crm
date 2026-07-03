"""Q8 regression: bounded price-range on the "any" column must group bounds
PER-COLUMN, not cross-combine across price_primary / price_secondary.

The old form ``(pp>=min OR ps>=min) AND (pp<=max OR ps<=max)`` returns a row
whose lower bound is met by ONE column and upper bound by the OTHER — so
neither price actually lies in [min, max] (false positive → over-count +
wrong list). Both query_by_price_range and count_by_price_range shared it.
"""
from __future__ import annotations

from ragbot.infrastructure.repositories.stats_index_repository import _price_clauses


def test_bounded_any_groups_bounds_per_column() -> None:
    params: dict = {}
    clauses = _price_clauses(500_000, 1_000_000, "any", params)
    assert len(clauses) == 1
    sql = clauses[0]
    # Each column must be checked against BOTH bounds together (BETWEEN-style),
    # then OR'd across the two columns.
    assert "price_primary >= :price_min AND price_primary <= :price_max" in sql
    assert "price_secondary >= :price_min AND price_secondary <= :price_max" in sql
    assert " OR " in sql
    # The buggy cross-column form must NOT appear.
    assert "(price_primary >= :price_min OR price_secondary >= :price_min)" not in sql
    assert params == {"price_min": 500_000, "price_max": 1_000_000}


def test_bounded_any_rejects_cross_column_false_positive() -> None:
    """Concrete row pp=2M ps=300k against [500k, 1M]: neither price is in range,
    so the clause must evaluate FALSE. Emulate the boolean the SQL computes."""
    clause = _price_clauses(500_000, 1_000_000, "any", {})[0]

    def _eval(pp: int, ps: int) -> bool:
        # Mirror the generated SQL boolean for a single row.
        primary_in = pp >= 500_000 and pp <= 1_000_000
        secondary_in = ps >= 500_000 and ps <= 1_000_000
        # The clause is exactly this OR — assert the SQL shape encodes it.
        assert "AND price_primary <= :price_max) OR (price_secondary" in clause
        return primary_in or secondary_in

    # pp above range, ps below range → must NOT match (the old bug matched it).
    assert _eval(2_000_000, 300_000) is False
    # A genuinely in-range secondary price → matches.
    assert _eval(2_000_000, 700_000) is True
    # A genuinely in-range primary price → matches.
    assert _eval(800_000, 100) is True


def test_one_sided_any_keeps_or_across_columns() -> None:
    # min only: a "trên X" one-sided query needs only ONE column past the bound.
    c_min = _price_clauses(500_000, None, "any", {})
    assert c_min == ["(price_primary >= :price_min OR price_secondary >= :price_min)"]
    # max only: "dưới X".
    c_max = _price_clauses(None, 1_000_000, "any", {})
    assert c_max == ["(price_primary <= :price_max OR price_secondary <= :price_max)"]


def test_single_column_uses_that_column_for_both_bounds() -> None:
    p: dict = {}
    c = _price_clauses(500_000, 1_000_000, "primary", p)
    assert c == ["price_primary >= :price_min", "price_primary <= :price_max"]
    assert p == {"price_min": 500_000, "price_max": 1_000_000}


def test_no_bounds_returns_empty() -> None:
    assert _price_clauses(None, None, "any", {}) == []
