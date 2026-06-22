"""Price-lock extraction (generate node) — delimiter-aware for happy-case markdown.

Locks the fix for control-register items B (domain-neutral generic keys) + C
(CSV-only extraction failed on happy-case `| name | price |` markdown tables). The
helper must read prices from BOTH markdown pipe tables and legacy CSV rows. Fixtures
are domain-neutral (generic "Item A") — shape-based, no service/brand literal.
"""
from __future__ import annotations

from ragbot.orchestration.nodes.generate import _extract_locked_prices


def test_happy_case_markdown_pipe_table():
    """Happy-case data is a markdown pipe table — the old CSV split failed here."""
    preview = "| Tên | Giá lẻ | Giá gốc |\n| --- | --- | --- |\n| Item A | 700000 | 800000 |"
    assert _extract_locked_prices(preview, "item a") == ("700000", "800000")


def test_legacy_csv_row_still_works():
    """Legacy CSV rows must keep working (back-compat)."""
    preview = "4,Item A,199000,800000"
    assert _extract_locked_prices(preview, "item a") == ("199000", "800000")


def test_single_price_markdown():
    preview = "| Item A | 500000 |"
    assert _extract_locked_prices(preview, "item a") == ("500000", None)


def test_service_absent_returns_none():
    preview = "| Item B | 100000 |\n| Item C | 200000 |"
    assert _extract_locked_prices(preview, "item a") == (None, None)


def test_no_price_cell_returns_none():
    preview = "| Item A | liên hệ |"
    assert _extract_locked_prices(preview, "item a") == (None, None)
