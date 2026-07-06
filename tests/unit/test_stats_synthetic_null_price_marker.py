"""002-F: the stats synthetic chunk must give the LLM an explicit price-absent
signal for a null-price entity when priced siblings share the served set.

Root cause (truth-audit 002, SỰ THẬT — evidence step7_final_verdicts.json B-001):
the reference bot's `2-R16 195/65 NEO` row has price_primary=NULL (verified in
document_service_index). The DSI point-lookup retrieved it correctly (top-1,
score 0.5419), but the serialized synthetic chunk emitted NO price field for it
(name-only line), so the generator borrowed the adjacent priced Rovelo row's
1.350.000 — a cross-row misattribution (verdict `lech`). The fix: when the
served set mixes priced + price-less rows, a null-price entity emits an explicit
structural absent-marker for its price column, so the LLM sees "this cell is
empty" instead of a gap it fills from a neighbour.

Domain-neutral: the marker is a schema-level structural token (constant), never
a corpus/brand/language literal. A set with NO priced rows at all (a delivery
sheet that has no price column) must NOT gain a price marker — the marker only
disambiguates the mixed case.
"""
from __future__ import annotations

from ragbot.orchestration.query_graph import _serialize_stats_entity_row
from ragbot.shared.constants import (
    DEFAULT_STATS_ATTR_MAX_CHARS,
    DEFAULT_STATS_ATTR_MAX_WORDS,
    STATS_NULL_PRICE_MARKER,
)

_KW = {
    "attr_max_chars": DEFAULT_STATS_ATTR_MAX_CHARS,
    "attr_max_words": DEFAULT_STATS_ATTR_MAX_WORDS,
    "null_price_marker": STATS_NULL_PRICE_MARKER,
}

# Real shape (neutralized): the price-NULL shell that B-001 mis-answered.
_NULL_PRICE_ENTITY = {
    "entity_name": "195/65R16 NEOBRAND",
    "price_primary": None,
    "price_secondary": None,
    "entity_category": "",
    "attributes_json": {"productname": "Lốp NEOBRAND 195/65R16", "date1": "26"},
}
# A priced sibling that shares the served synthetic chunk.
_PRICED_SIBLING = {
    "entity_name": "195/75R16 RVLBRAND",
    "price_primary": 1350000,
    "price_secondary": None,
    "entity_category": "",
    "attributes_json": {"quantity": "14"},
}


def test_null_price_entity_emits_absent_marker_when_siblings_priced() -> None:
    """RED target: with priced siblings present, the null-price row must carry an
    EXPLICIT price-absent marker — at pre-fix HEAD it emitted a name-only line
    (no price field) so the LLM borrowed the neighbour's number."""
    line = _serialize_stats_entity_row(_NULL_PRICE_ENTITY, chunk_has_price=True, **_KW)
    assert line is not None
    assert f"price: {STATS_NULL_PRICE_MARKER}" in line, line
    # The borrowed neighbour value must NEVER appear on this entity's line.
    assert "1350000" not in line


def test_priced_entity_line_unchanged() -> None:
    line = _serialize_stats_entity_row(_PRICED_SIBLING, chunk_has_price=True, **_KW)
    assert line is not None
    assert "195/75R16 RVLBRAND: 1350000" in line
    assert STATS_NULL_PRICE_MARKER not in line


def test_all_priceless_set_gets_no_price_marker() -> None:
    """A delivery-sheet-style set with NO priced row must not sprout a price
    marker — the marker only disambiguates the mixed (priced + null) case."""
    line = _serialize_stats_entity_row(_NULL_PRICE_ENTITY, chunk_has_price=False, **_KW)
    assert line is not None
    assert f"price: {STATS_NULL_PRICE_MARKER}" not in line
    # The row still surfaces its groundable attributes (date/productname).
    assert "date1: 26" in line


def test_non_field_like_name_null_price_still_marks_absence() -> None:
    """A variant mega-cell name is dropped from the lead, but the price column
    absence is still marked so the bare-number ambiguity never invites a grab."""
    mega = {
        "entity_name": ", ".join(f"195/65R16 alias{i}" for i in range(40)),
        "price_primary": None,
        "price_secondary": None,
        "entity_category": "",
        "attributes_json": {"productname": "Lốp NEOBRAND 195/65R16"},
    }
    line = _serialize_stats_entity_row(mega, chunk_has_price=True, **_KW)
    assert line is not None
    assert f"price: {STATS_NULL_PRICE_MARKER}" in line
