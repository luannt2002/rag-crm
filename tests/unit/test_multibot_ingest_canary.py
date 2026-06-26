"""[T1-Smartness] Multi-bot ingest CANARY — the proof that the extractor is
domain-neutral, not patched for spa/xe.

"Fix chuẩn cho multi-bot" is GUARANTEED only by testing the SAME engine against
several INDEPENDENT domains + the two structural failure shapes found in the xe
deep-dive, expressed domain-neutrally:

  Shape S1 — split (2-row) header: row 1 names the first columns, row 2 names the
             rest (with empty leads). Row-2 names must NOT be lost to col_N.
  Shape S2 — the NAME column is NOT col-0: col-0 is a long delimited synonym/alias
             blob; the real identifier is a later column. The row must NOT be
             dropped, and the name must come from the identifier column.

A fix is multi-bot-correct iff EVERY domain below extracts the entity name + keeps
its labelled attributes — phone, legal, real-estate, English, AND the xe-shaped
rows — with NO per-domain code. Domains use per-bot ``custom_roles`` (the owner-
declared, domain-neutral path) where the header is fully custom.

Tests that currently FAIL document the engine gaps the multi-bot fix must close;
they are the executable spec.
"""
from __future__ import annotations

import random

import pytest

from ragbot.shared.document_stats import parse_table_chunks


def _attrs(ents: list) -> dict:
    out: dict = {}
    for e in ents:
        out.update(e.attributes)
    return out


# ── baseline: diverse domains via owner-declared roles (already supported) ──────
def test_phone_domain_clean() -> None:
    content = "Model,RAM,Pin,Giá bán\niPhone 15,8GB,3300mAh,25000000\n"
    ents = parse_table_chunks(
        [{"content": content}],
        {"Model": "name", "RAM": "attribute", "Pin": "attribute", "Giá bán": "value"},
    )
    assert ents and ents[0].name == "iPhone 15"
    assert "8GB" in {str(v) for v in _attrs(ents).values()}


def test_legal_domain_clean() -> None:
    content = "Điều,Tiêu đề,Nội dung\nĐiều 5,Phạm vi,Quy định chung áp dụng\n"
    ents = parse_table_chunks(
        [{"content": content}], {"Điều": "name", "Tiêu đề": "attribute", "Nội dung": "attribute"}
    )
    assert ents and ents[0].name == "Điều 5"


def test_realestate_domain_clean() -> None:
    content = "Mã căn,Diện tích,Hướng,Giá\nA-12,75m2,Đông Nam,3500000000\n"
    ents = parse_table_chunks(
        [{"content": content}],
        {"Mã căn": "name", "Diện tích": "attribute", "Hướng": "attribute", "Giá": "value"},
    )
    assert ents and ents[0].name == "A-12"
    assert "75m2" in {str(v) for v in _attrs(ents).values()}


# ── Shape S1 — split 2-row header (xe-1 shape, domain-neutral) ───────────────────
@pytest.mark.xfail(
    reason="SPEC for the multi-bot ingest fix (Trụ 1): split-header merge + "
    "leading-empty-column alignment must label row-2 columns. Pending — fix at the "
    "tabular_markdown layer + re-ingest.",
    strict=False,
)
def test_s1_split_header_labels_row2_columns() -> None:
    content = (
        ",Kho,Mã,Tên hàng,,\n"
        ",,,,date1,ảnh\n"
        "K1,X9,Sản phẩm Alpha,26,http://img/a\n"
    )
    ents = parse_table_chunks([{"content": content}])
    assert ents, "split-header row produced no entity"
    keys = " ".join(_attrs(ents)).lower()
    assert "date1" in keys, f"date1 lost to col_N: {list(_attrs(ents))}"
    assert "ảnh" in keys or "anh" in keys, f"image col lost to col_N: {list(_attrs(ents))}"


# ── Shape S2 — name column is NOT col-0 (xe-3 shape: synonym-blob col-0) ─────────
def test_s2_blob_alias_col0_does_not_drop_row() -> None:
    # col-0 is a long ;/comma synonym blob; the real id is "code"/"productname".
    blob = ", ".join(f"variant {i}" for i in range(40))  # long delimited alias list
    content = (
        f'question,code,productname,quantity,price\n'
        f'"{blob}",AB-12,Sản phẩm Beta,404,1500000\n'
    )
    ents = parse_table_chunks([{"content": content}])
    assert ents, "S2: whole row dropped because col-0 is a synonym blob"
    names = [e.name for e in ents]
    assert "Sản phẩm Beta" in names or "AB-12" in names, (
        f"name must come from the identifier column, not col-0 blob: {names}"
    )
    vals = {str(v) for v in _attrs(ents).values()}
    assert "404" in vals, f"quantity(stock) 404 not captured as a labelled attr: {_attrs(ents)}"


# ── ATTRIBUTE-GENERIC: a non-price column whose header collides with a category
# token ("Tồn kho" stock vs "kho" warehouse) must keep its value as a labelled
# attribute — a stock count is NOT a price and must never be dropped (xe-7 bug:
# "tồn kho 165/65R14" answered the price 702 instead of the stock 404). ─────────
def test_stock_column_value_not_dropped_no_roles() -> None:
    content = "Tên hàng,Tồn kho,Đơn giá\n165/65R14,404,702000\n"
    ents = parse_table_chunks([{"content": content}])
    assert ents, "stock row produced no entity"
    e = ents[0]
    assert e.name == "165/65R14"
    assert e.price_primary == 702000  # price stays the price
    vals = {str(v) for v in _attrs([e]).values()}
    keys = " ".join(_attrs([e])).lower()
    # the stock count must be retrievable under its OWN column label, NOT lost.
    assert "404" in vals, f"stock 404 dropped (price-bias): name={e.name} attrs={e.attributes}"
    assert "ton kho" in keys or "kho" in keys, (
        f"stock column label lost: {list(_attrs([e]))}"
    )
    # the stock count must NOT be confused with the price.
    assert e.price_primary != 404


# A small non-price integer in a generic (non-price) column survives as an
# attribute — the ingest price-floor must apply ONLY to price columns, never to
# arbitrary numeric attributes (quantity, count, code). Domain-neutral.
def test_small_number_in_generic_column_survives() -> None:
    content = "Mã,Số lượng,Giá\nSP-1,7,500000\n"
    ents = parse_table_chunks([{"content": content}])
    assert ents
    vals = {str(v) for v in _attrs(ents).values()}
    assert "7" in vals, f"small count 7 floored away: {_attrs(ents)}"
    assert ents[0].price_primary == 500000


# ── OPTIONAL OVERRIDE (column_roles): when the owner DECLARES a numeric column as
# a generic 'attribute', a value that parses as money (a large stock count) must
# NOT be hijacked into a price — the declaration is authoritative over shape. ────
def test_declared_attribute_numeric_not_read_as_price() -> None:
    content = "Tên,Số lượng tồn,Đơn giá\n165R,40400,702000\n"
    ents = parse_table_chunks(
        [{"content": content}],
        {"Tên": "name", "Số lượng tồn": "attribute", "Đơn giá": "value"},
    )
    assert ents
    e = ents[0]
    assert e.price_primary == 702000, f"price mis-bound: {e.price_primary}"
    # the stock count stays a labelled attribute, never a (secondary) price.
    assert e.price_secondary is None, f"stock leaked into price_secondary: {e.price_secondary}"
    assert str(e.attributes.get("Số lượng tồn")) == "40400", f"stock attr lost: {e.attributes}"


# ── HEADER ROBUST: a section-title line above a sub-table must NOT be eaten as a
# data value, AND its rows bind to it as the section. (xe "Kho lốp ROVELO"). ─────
def test_section_title_line_not_a_data_value() -> None:
    content = (
        "Kho lốp ROVELO\n"
        "Tên hàng,Tồn kho,Đơn giá\n"
        "165/65R14,404,702000\n"
    )
    ents = parse_table_chunks([{"content": content}])
    assert ents, "rows under a section title produced no entity"
    names = [e.name for e in ents]
    assert "165/65R14" in names
    # the title is a category for the row, never a fake entity name.
    assert "Kho lốp ROVELO" not in names
    assert any(e.category == "Kho lốp ROVELO" for e in ents), (
        f"section title not bound as category: {[(e.name, e.category) for e in ents]}"
    )


# ── English domain via structural inference (no custom roles) ────────────────────
def test_english_domain_inferred() -> None:
    content = "Item,Category,Price\nWidget Pro,Tools,500000\n"
    ents = parse_table_chunks([{"content": content}])
    assert ents and ents[0].name == "Widget Pro"
    assert ents[0].price_primary == 500000


# ── INVARIANT (property-based) — the proof for the (N+1)th UNKNOWN bot ───────────
# Enumerating known domains can't prove correctness for a future bot. These
# invariants are tested on RANDOMLY-generated tables (random column names = an
# unseen domain) so they cover the unbounded N: for ANY well-formed table the
# engine must (a) not silently drop rows, and (b) lose no labelled value.
@pytest.mark.parametrize("seed", range(25))
def test_invariant_random_domain_no_silent_row_drop(seed: int) -> None:
    rng = random.Random(seed)
    ncols = rng.randint(3, 6)
    # Random, never-before-seen header names (simulate any new bot's columns).
    headers = [f"Field{rng.randint(100, 999)}{chr(65 + i)}" for i in range(ncols)]
    nrows = rng.randint(2, 5)
    rows = [
        [f"v{seed}r{r}c{c}" for c in range(ncols)]  # short identifier-like values
        for r in range(nrows)
    ]
    content = ",".join(headers) + "\n" + "\n".join(",".join(r) for r in rows)
    ents = parse_table_chunks([{"content": content}])
    # INV-1: a well-formed N-row table for an unseen domain must not vanish.
    assert ents, f"seed={seed}: rows silently dropped for an unseen domain"
    # INV-2: no labelled value is lost — every non-name cell value is retrievable
    # somewhere (name / price / category / aliases / attributes).
    surfaced = set()
    for e in ents:
        surfaced.add(e.name)
        surfaced.update(str(v) for v in e.attributes.values())
        if e.category:
            surfaced.add(e.category)
    flat = {c for row in rows for c in row}
    lost = flat - surfaced
    assert not lost, f"seed={seed}: values lost to nowhere (not name/attr/cat): {lost}"


# INV-3 (property-based): a SMALL non-price integer (stock/qty/count, below the
# price floor) in an unseen non-price column must survive as a labelled value for
# ANY domain — the price floor must never silence a generic numeric attribute.
@pytest.mark.parametrize("seed", range(25))
def test_invariant_small_numeric_attribute_not_floored(seed: int) -> None:
    rng = random.Random(seed)
    small = rng.randint(1, 999)  # below DEFAULT_PRICE_MIN_VND — a count, not money
    price = rng.randint(100_000, 9_000_000)
    # name col + a random non-price numeric col + a real price col.
    qty_hdr = f"Đếm{rng.randint(10, 99)}"  # unseen quantity-like header
    content = (
        f"Tên,{qty_hdr},Giá\n"
        f"Item-{seed},{small},{price}\n"
    )
    ents = parse_table_chunks([{"content": content}])
    assert ents, f"seed={seed}: row with a small numeric attribute dropped"
    surfaced = set()
    for e in ents:
        surfaced.update(str(v) for v in e.attributes.values())
    assert str(small) in surfaced, (
        f"seed={seed}: small count {small} floored away: {[e.attributes for e in ents]}"
    )
    # the real price is still picked up as the price.
    assert any(e.price_primary == price for e in ents), (
        f"seed={seed}: price {price} not bound"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
