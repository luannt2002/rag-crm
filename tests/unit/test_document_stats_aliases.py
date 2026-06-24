"""[T1-Smartness] Pin tests — Aliases / synonym column role in document_stats.

Root cause (verified): the stats-index column-role detector recognised ONLY
name/price/category via closed-vocab exact-match. An Aliases/synonym column was
dropped to ``attributes_json`` (unsearchable), so ``query_by_name_keyword`` could
not match a search-variant whose ``entity_name`` uses a different notation
(e.g. "265/50ZR20" vs query "265/50R20", though both rows list "265/50R20" in
their Aliases column).

These tests pin the Aliases ROLE: detection, capture into ``ParsedEntity.aliases``,
and the guarantees that the aliases cell does NOT become the name and does NOT
flood the attributes dict. Domain-neutral — generic header tokens, no bot/brand
literal. HALLU=0: every assertion is a concrete deterministic value.
"""
from __future__ import annotations

from ragbot.shared.document_stats import (
    _ALIASES_COL_TOKENS,
    _column_roles,
    parse_table_chunks,
)


def _make_chunk(content: str) -> dict:
    return {"content": content}


# ===========================================================================
# _ALIASES_COL_TOKENS — domain-neutral synonym/keyword header vocabulary
# ===========================================================================


def test_aliases_col_tokens_are_normalised_accent_stripped() -> None:
    """Tokens are stored normalised (lower-case, accent-stripped) so a header
    "Từ khoá" / "Biến thể" matches after _normalise()."""
    assert "tu khoa" in _ALIASES_COL_TOKENS
    assert "bien the" in _ALIASES_COL_TOKENS
    assert "aliases" in _ALIASES_COL_TOKENS
    assert "synonyms" in _ALIASES_COL_TOKENS
    assert "keyword" in _ALIASES_COL_TOKENS


# ===========================================================================
# _column_roles — detect the aliases column index
# ===========================================================================


def test_column_roles_detects_aliases_index() -> None:
    """A header carrying an aliases token assigns the ``aliases`` role index."""
    roles = _column_roles(["Tên", "Giá", "Aliases"])
    assert roles["name"] == 0
    assert roles["price"] == [1]
    assert roles["aliases"] == 2


def test_column_roles_aliases_synonym_header_variants() -> None:
    """Accented VN synonym headers resolve to the aliases role."""
    roles = _column_roles(["Tên", "Từ khoá", "Giá"])
    assert roles["aliases"] == 1


def test_column_roles_no_aliases_when_absent() -> None:
    """No aliases token → aliases role is None (backward compatible)."""
    roles = _column_roles(["Tên", "Nhóm", "Giá"])
    assert roles["aliases"] is None


# ===========================================================================
# _extract_entity_from_row — capture the aliases cell into ParsedEntity.aliases
# ===========================================================================


def test_parse_table_chunks_captures_aliases() -> None:
    """The aliases cell lands in ``entity.aliases`` (not name, not a flooded attr)."""
    content = (
        "Tên,Giá,Aliases\n"
        'Lốp A,684000,"265/50R20; 265 50 R20; 265/50/20"\n'
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    e = entities[0]
    assert e.name == "Lốp A"
    assert e.price_primary == 684_000
    assert e.aliases is not None
    assert "265/50R20" in e.aliases


def test_aliases_cell_does_not_become_name() -> None:
    """When the name column precedes the aliases column, the aliases value must
    NOT be promoted to the entity name."""
    content = (
        "Tên,Aliases,Giá\n"
        'Sản phẩm X,"variant-1; variant-2",500000\n'
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    assert entities[0].name == "Sản phẩm X"
    assert entities[0].aliases is not None
    assert "variant-1" in entities[0].aliases


def test_aliases_does_not_flood_attributes() -> None:
    """The aliases cell is captured into the dedicated field, not dumped into
    attributes_json under its header key."""
    content = (
        "Tên,Giá,Aliases\n"
        'Dịch vụ Y,300000,"kw1; kw2; kw3"\n'
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    e = entities[0]
    assert e.aliases is not None
    # The aliases header key must NOT appear in attributes (it has its own field).
    assert "Aliases" not in e.attributes
    assert not any("kw1" in str(v) for v in e.attributes.values())


def test_aliases_none_when_no_aliases_column() -> None:
    """A catalog with no aliases column yields entity.aliases == None."""
    content = (
        "Tên,Giá\n"
        "Service Z,499000\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    assert entities[0].aliases is None


def test_aliases_empty_cell_is_none() -> None:
    """An empty aliases cell yields None, not an empty string."""
    content = (
        "Tên,Giá,Aliases\n"
        "Service W,499000,\n"
    )
    entities = parse_table_chunks([_make_chunk(content)])
    assert len(entities) == 1
    assert entities[0].aliases is None
