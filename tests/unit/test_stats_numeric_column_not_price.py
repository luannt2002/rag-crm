"""[Deep-debug ING-F1] A stock/quantity/date column must NOT become the price.

When a header carries an explicit price column ("Giá"), any OTHER money-shaped
column ("SL tồn: 40400", a date integer) is a stock/date, not a second price.
Folding it into price_primary (and demoting the real price to price_secondary)
was the Q13-class field-map bug. The real price stays price_primary; the numeric
non-price column becomes a labelled attribute.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks


def _one(md: str):
    return parse_table_chunks([{"content": md}])[0]


def test_stock_column_not_read_as_price() -> None:
    e = _one("| Tên | SL tồn | Giá |\n| --- | --- | --- |\n| Sản phẩm A | 40400 | 129000 |")
    assert e.price_primary == 129000, "the Giá column must be the price"
    assert e.price_secondary is None, "the stock count must NOT become price_secondary"
    assert str((e.attributes or {}).get("SL tồn")) == "40400", "stock stays a labelled attr"


def test_date_integer_not_price() -> None:
    e = _one("| Tên | Ngày nhập | Giá |\n| --- | --- | --- |\n| Sản phẩm B | 20241224 | 150000 |")
    assert e.price_primary == 150000
    assert e.price_secondary != 20241224


def test_lone_unlabeled_price_still_works() -> None:
    """When there is NO recognised price column, the money fallback still fires."""
    e = _one("| Tên | 500000 |\n| --- | --- |\n| Dịch vụ X | 500000 |")
    assert e.price_primary == 500000
