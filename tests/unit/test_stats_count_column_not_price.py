"""CS1-a: a NON-price numeric column (quantity / stock / measure) must NOT be
mis-read as a price by the unknown-pure-money → price fallback.

Root cause (2026-07-13 flow audit): ``_is_pure_money("40400")`` is True
(``parse_money_vn`` floor 10_000), so a large COUNT cell in a column whose
header carries no price role — and which the owner did not declare as an
``attribute`` — falls through ``elif _is_pure_money(col)`` and becomes
``price_secondary``. Served via the live ``stats_index_route`` that number
surfaces to the user as a PRICE (the fabricated-price class, bug#13).

Fix: recognise generic count/measure header words (domain-neutral structure
vocab, same policy as the price/name sets) as a ``count`` role → routed to
labelled attributes, never a price. Zero price-recall regression: a count
header is never a price column.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks


def _chunk(content: str) -> dict:
    return {"content": content}


def test_recognised_quantity_header_not_read_as_price() -> None:
    """English 'quantity' header, large count → attribute, never price."""
    ents = parse_table_chunks([_chunk("Name,Price,Quantity\nTyre A,1200000,40400\n")])
    assert len(ents) == 1
    e = ents[0]
    assert e.price_primary == 1_200_000
    assert e.price_secondary is None, f"count leaked as price: {e.price_secondary}"


def test_vietnamese_quantity_header_not_read_as_price() -> None:
    """VN 'Số lượng' header, large count → attribute, never price (the flaw's
    literal example)."""
    ents = parse_table_chunks([_chunk("Tên,Giá,Số lượng\nLốp A,1200000,40400\n")])
    assert len(ents) == 1
    e = ents[0]
    assert e.price_primary == 1_200_000
    assert e.price_secondary is None, f"quantity leaked as price: {e.price_secondary}"


def test_stock_count_value_kept_as_attribute() -> None:
    """The count is not dropped — it stays a labelled, retrievable attribute."""
    ents = parse_table_chunks([_chunk("Tên,Giá,Tồn kho\nLốp A,1200000,40400\n")])
    e = ents[0]
    assert e.price_primary == 1_200_000
    assert e.price_secondary is None
    # 40400 preserved somewhere in attributes (label = the header), not a price
    assert any("40400" in str(v) or v == 40400 for v in e.attributes.values()), (
        f"count value lost entirely: {e.attributes}"
    )


def test_real_secondary_price_out_of_vocab_header_still_recalled() -> None:
    """RECALL GUARD: a genuine 2nd price column whose header is out-of-price-
    vocab AND not a count word must STILL be picked up by the pure-money
    fallback — the count-role fix must not over-exclude legitimate prices."""
    ents = parse_table_chunks([_chunk("Tên,Giá,Phụ thu\nLốp A,1200000,300000\n")])
    e = ents[0]
    assert e.price_primary == 1_200_000
    assert e.price_secondary == 300_000, "legit out-of-vocab secondary price lost"
