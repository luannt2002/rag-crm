"""[T1-Smartness] Regression — stats-index entity dedup before bulk_insert.

The ``table_dual_index`` chunker emits each catalog row BOTH as its own
single-row chunk AND inside a multi-row group/summary chunk, so the
deterministic stats extractor (``parse_table_chunks``) yields the same logical
entity many times. ``_dedup_stats_entities`` collapses those duplicates BEFORE
``StatsIndexRepository.bulk_insert`` so the index holds one row per real
service/product and count/aggregate queries stop over-counting.

These tests pin the dedup CONTRACT with concrete deterministic assertions
(no LLM, no DB):

- exact (name, price) duplicates collapse to one row;
- a priced row beats an unpriced duplicate of the same name;
- two genuinely-distinct services sharing a name but with DIFFERENT prices
  both survive (no over-dedup);
- the richest member of a duplicate group is the one kept;
- output is order-deterministic (no dict-hash / RNG reliance);
- empty input is a no-op.
"""
from __future__ import annotations

from ragbot.application.services.document_service.ingest_stages_final import (
    _dedup_stats_entities,
)
from ragbot.shared.document_stats import ParsedEntity


def _ent(
    name: str,
    price_primary: int | None = None,
    *,
    category: str | None = None,
    price_secondary: int | None = None,
    chunk_index: int = 0,
    attributes: dict | None = None,
) -> ParsedEntity:
    return ParsedEntity(
        name=name,
        category=category,
        price_primary=price_primary,
        price_secondary=price_secondary,
        chunk_index=chunk_index,
        attributes=attributes or {},
    )


def _keyset(entities: list[ParsedEntity]) -> set[tuple[str, int | None]]:
    return {(e.name, e.price_primary) for e in entities}


def test_exact_duplicates_collapse_to_one() -> None:
    """Same name + same price repeated (the dual-index group×row duplication)
    collapses to a SINGLE entity."""
    entities = [
        _ent("205/55R16 91V CITYTRAXX", 800_000, chunk_index=i) for i in range(7)
    ]
    out = _dedup_stats_entities(entities)
    assert len(out) == 1
    assert out[0].name == "205/55R16 91V CITYTRAXX"
    assert out[0].price_primary == 800_000


def test_priced_row_beats_unpriced_duplicate() -> None:
    """A summary chunk can surface the same product with an EMPTY price cell.
    The priced row wins; the null-price duplicate of the same name is dropped."""
    entities = [
        _ent("195/65R15 91H CITYTRAXX", None, chunk_index=0),   # unpriced dup
        _ent("195/65R15 91H CITYTRAXX", 700_000, chunk_index=1),  # the real row
        _ent("195/65R15 91H CITYTRAXX", None, chunk_index=2),   # unpriced dup
    ]
    out = _dedup_stats_entities(entities)
    assert len(out) == 1, "unpriced duplicate must collapse into the priced row"
    assert out[0].price_primary == 700_000


def test_distinct_prices_same_name_both_survive() -> None:
    """Two genuinely-distinct services with the SAME name but DIFFERENT prices
    are not the same entity — both must survive (price is in the dedup key)."""
    entities = [
        _ent("Gói combo", 1_000_000, chunk_index=0),
        _ent("Gói combo", 2_000_000, chunk_index=1),
        # each repeated once (dual-index duplication) — still only 2 distinct
        _ent("Gói combo", 1_000_000, chunk_index=2),
        _ent("Gói combo", 2_000_000, chunk_index=3),
    ]
    out = _dedup_stats_entities(entities)
    assert _keyset(out) == {("Gói combo", 1_000_000), ("Gói combo", 2_000_000)}
    assert len(out) == 2


def test_accent_and_case_insensitive_collapse() -> None:
    """Dedup key normalises accents + case, so a diacritic/case variant of the
    same name at the same price collapses (matches the stats parser's
    ``_normalise``-based identity)."""
    entities = [
        _ent("Lốp ROVELO", 500_000, chunk_index=0),
        _ent("lop rovelo", 500_000, chunk_index=1),  # accent/case variant
    ]
    out = _dedup_stats_entities(entities)
    assert len(out) == 1


def test_richest_member_is_kept() -> None:
    """Among exact (name, price) duplicates, the kept row is the RICHEST: the
    one carrying a secondary price + more attributes (a per-row chunk that did
    not lose columns) beats the stripped group-chunk copy."""
    poor = _ent("Item A", 300_000, chunk_index=0)
    rich = _ent(
        "Item A",
        300_000,
        price_secondary=450_000,
        category="Nhóm 1",
        attributes={"warranty": "12m", "size": "16"},
        chunk_index=1,
    )
    # Order: poor first, rich second — kept entity must be the rich one
    # regardless of input position.
    out = _dedup_stats_entities([poor, rich])
    assert len(out) == 1
    assert out[0].price_secondary == 450_000
    assert out[0].attributes == {"warranty": "12m", "size": "16"}
    # And the reverse input order keeps the same (rich) winner — position-stable.
    out_rev = _dedup_stats_entities([rich, poor])
    assert len(out_rev) == 1
    assert out_rev[0].price_secondary == 450_000


def test_unpriced_only_entity_survives() -> None:
    """A genuinely price-less catalog entry (no priced sibling) keeps its single
    row so it stays searchable — the priced-beats-unpriced rule must not erase
    a name that simply has no price anywhere."""
    entities = [
        _ent("Tên kho LANDSPIDER", None, chunk_index=0),
        _ent("Tên kho LANDSPIDER", None, chunk_index=1),  # pure duplicate
    ]
    out = _dedup_stats_entities(entities)
    assert len(out) == 1
    assert out[0].price_primary is None


def test_mixed_corpus_distinct_set_and_counts() -> None:
    """End-to-end shape: a small mixed batch with intentional duplicates +
    a priced/unpriced dup + two distinct-priced same-name rows collapses to the
    exact expected unique set, and NO distinct priced entity is lost."""
    entities = [
        # product P1 seen 4× at one price (dual-index duplication)
        *[_ent("P1", 100_000, chunk_index=i) for i in range(4)],
        # P2 priced + an unpriced summary dup of P2 → unpriced dropped
        _ent("P2", 250_000, chunk_index=10),
        _ent("P2", None, chunk_index=11),
        # P3 two distinct prices → both survive
        _ent("P3", 1_000_000, chunk_index=20),
        _ent("P3", 3_000_000, chunk_index=21),
        # P4 unpriced-only → survives once
        _ent("P4", None, chunk_index=30),
        _ent("P4", None, chunk_index=31),
    ]
    out = _dedup_stats_entities(entities)
    assert _keyset(out) == {
        ("P1", 100_000),
        ("P2", 250_000),
        ("P3", 1_000_000),
        ("P3", 3_000_000),
        ("P4", None),
    }
    # 5 unique logical entities collapsed from 11 raw extractions.
    assert len(out) == 5
    distinct_priced = {(e.name, e.price_primary) for e in out if e.price_primary is not None}
    assert distinct_priced == {
        ("P1", 100_000),
        ("P2", 250_000),
        ("P3", 1_000_000),
        ("P3", 3_000_000),
    }


def test_deterministic_output_order() -> None:
    """Same input → identical output (keys + order). The dedup must not depend
    on dict hashing / RNG; first-seen insertion order drives the result."""
    entities = [
        _ent("B", 200_000, chunk_index=0),
        _ent("A", 100_000, chunk_index=1),
        _ent("B", 200_000, chunk_index=2),  # dup of B
        _ent("C", None, chunk_index=3),
        _ent("A", 100_000, chunk_index=4),  # dup of A
    ]
    out1 = [(e.name, e.price_primary) for e in _dedup_stats_entities(list(entities))]
    out2 = [(e.name, e.price_primary) for e in _dedup_stats_entities(list(entities))]
    assert out1 == out2
    # First-seen order preserved: B (idx0), A (idx1), C (idx3).
    assert out1 == [("B", 200_000), ("A", 100_000), ("C", None)]


def test_empty_input_is_noop() -> None:
    assert _dedup_stats_entities([]) == []
