"""F1 — structural (shape-not-vocab) header detection in document_stats.

THE ONE LAW: header-ness is decided by FORM (a row sitting directly above a
``| --- |`` separator the converter emitted), NOT by a Vietnamese/English
commercial vocabulary word-list. A correctly-shaped non-VN / non-VND header
(English shipping manifest, Spanish catalog, legal column names) must be
recognised so its rows bind to the REAL column labels instead of collapsing to
positional ``col_N`` placeholders.

Regression guard for the ``col_N`` CRUX (dual-oracle drift): the structured
markdown produced by ``tabular_markdown`` carries the separator; the extractor
must trust it.
"""
from __future__ import annotations

import re

from ragbot.shared.document_stats import parse_table_chunks

_COL_N_RE = re.compile(r"^col_?\d+$", re.IGNORECASE)


def _attr_keys(entities) -> set[str]:
    keys: set[str] = set()
    for e in entities:
        keys.update(e.attributes.keys())
    return keys


def test_english_manifest_header_recognized_no_col_n() -> None:
    """A non-VN, non-VND English table with NO vocab-matching header word still
    binds to its real labels (via the | --- | separator) — zero col_N."""
    content = (
        "| MARKS | CARGO DESCRIPTION | NGÀY VỀ |\n"
        "| --- | --- | --- |\n"
        "| ABC123 | Steel pipes grade A | 2026-01-15 |\n"
        "| XYZ789 | Copper wire reel | 2026-02-20 |\n"
    )
    entities = parse_table_chunks([{"content": content}])

    assert len(entities) == 2, f"expected 2 data rows, got {len(entities)}"
    names = {e.name for e in entities}
    assert names == {"ABC123", "XYZ789"}, names

    keys = _attr_keys(entities)
    # The REAL header labels must be used as attribute keys …
    assert "CARGO DESCRIPTION" in keys, keys
    # … and NO positional col_N placeholder may leak through.
    assert not any(_COL_N_RE.match(k) for k in keys), f"col_N leaked: {keys}"


def test_spanish_catalog_header_recognized() -> None:
    """Spanish header (Producto | Precio) — 'producto'/'precio' are NOT in the
    VN/EN token set, yet the separator makes it a header."""
    content = (
        "| Producto | Precio | Stock |\n"
        "| --- | --- | --- |\n"
        "| Cafetera | 450000 | 12 |\n"
    )
    entities = parse_table_chunks([{"content": content}])
    assert len(entities) == 1
    keys = _attr_keys(entities)
    assert not any(_COL_N_RE.match(k) for k in keys), f"col_N leaked: {keys}"


def test_separator_row_above_data_is_not_promoted() -> None:
    """A real data row (carries a value) sitting where there is NO separator is
    NOT mis-detected as a header — guard against over-promotion."""
    content = (
        "| Sản phẩm | Giá |\n"
        "| --- | --- |\n"
        "| Áo thun | 199000 |\n"
        "| Quần jean | 350000 |\n"
    )
    entities = parse_table_chunks([{"content": content}])
    # 2 data rows, header consumed (not counted as an entity).
    assert len(entities) == 2, [e.name for e in entities]
    assert {e.name for e in entities} == {"Áo thun", "Quần jean"}
