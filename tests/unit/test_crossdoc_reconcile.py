"""[Phase 4] Cross-doc fragment reconcile (alias-cell digit-key match).

One physical product is listed across several sheets: the name + arrival-date in
one, the price + stock in another (the price sheet carries a comma-separated alias
cell of every spec spelling). A combined query ("giá + tồn + ngày về") then can't
gather the fields — the LLM deflects or fabricates. ``_reconcile_cross_doc`` merges
the price-LESS fragments INTO the priced anchor when their spec digit-key matches
the anchor's alias digit-keys, so one complete record surfaces. Conservative: never
merges two priced anchors (no price conflict), never merges on a <5-digit key (no
spurious 18/40 collision).
"""
from __future__ import annotations

from ragbot.orchestration.query_graph import _reconcile_cross_doc


def _anchor():
    return {
        "entity_name": "2-ZR18 235/40 LPD",
        "price_primary": 1602000,
        "attributes_json": {
            "question": "235/40R18, 235 40 18, 235/40ZR18, 2354018, Landspider 235/40R18",
            "quantity": "27",
            "price": "1602000",
        },
    }


def test_priceless_fragment_merged_into_anchor() -> None:
    anchor = _anchor()
    frag_catalog = {  # 11111: name + no price
        "entity_name": "Lốp xe LANDSPIDER 235/40ZR18 95WXL CITYTRAXX H/P",
        "price_primary": None,
        "attributes_json": {"Tên kho": "Kho LANDSPIDER", "date1": "26"},
    }
    frag_ship = {  # 2222: arrival date, no price
        "entity_name": "235/40ZR18 95WXL CITYTRAXX H/P",
        "price_primary": None,
        "attributes_json": {"NGÀY VỀ": "28-thg 11"},
    }
    out = _reconcile_cross_doc([anchor, frag_catalog, frag_ship])
    # the two price-less fragments collapse into the single priced anchor
    assert len(out) == 1
    merged = out[0]
    assert merged["price_primary"] == 1602000
    # the anchor absorbed the unique labelled fields (arrival date)
    assert merged["attributes_json"].get("NGÀY VỀ") == "28-thg 11"


def test_different_spec_not_merged() -> None:
    anchor = _anchor()  # 235/40R18
    other = {  # 235/40R19 — DIFFERENT product
        "entity_name": "Lốp xe LANDSPIDER 235/40ZR19 96WXL CITYTRAXX H/P",
        "price_primary": None,
        "attributes_json": {"NGÀY VỀ": "28-thg 11"},
    }
    out = _reconcile_cross_doc([anchor, other])
    assert len(out) == 2, "different spec must NOT merge"


def test_no_anchor_returns_unchanged() -> None:
    a = {"entity_name": "A", "price_primary": None, "attributes_json": {}}
    b = {"entity_name": "B", "price_primary": None, "attributes_json": {}}
    out = _reconcile_cross_doc([a, b])
    assert out == [a, b]


def test_two_priced_anchors_never_merged() -> None:
    a1 = _anchor()
    a2 = {
        "entity_name": "2-ZR18 235/40 RVL",
        "price_primary": 1550000,
        "attributes_json": {"question": "235/40R18, 2354018", "quantity": "5"},
    }
    out = _reconcile_cross_doc([a1, a2])
    # both are priced → both kept (a price conflict must never be silently merged)
    assert len(out) == 2
