"""[BUG A] Pure-col_N noise entities are dropped from the stats index.

A text/QA bot (legal circular) is not a catalog, but the stats extractor still
comma-splits its prose and pipe-splits its letterhead into "entities" whose only
structured content is ``col_N`` placeholders. Their synthetic chunk (score 1.0)
then dominates real prose retrieval and leaks ``col_1:`` into the answer. The
extractor must drop such noise rows while keeping every real catalog row.
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks


def _entities(md: str):
    return parse_table_chunks([{"content": md, "raw_chunk": md}])


def test_legal_letterhead_and_prose_dropped() -> None:
    """The letterhead pipe-row + comma-split prose → 0 col_N noise entities."""
    md = (
        "| NGÂN HÀNG NHÀ NƯỚC | CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM |\n"
        "| --- | --- |\n"
        "| Hà Nội | ngày 21 tháng 10 năm 2020 |\n"
    )
    ents = _entities(md)
    for e in ents:
        assert not any(
            str(k).startswith("col_") for k in (e.attributes or {})
        ), f"col_N noise entity survived: {e.name} {e.attributes}"


def test_catalog_row_with_price_kept() -> None:
    """A real priced catalog row is NEVER dropped."""
    md = (
        "| Tên | Giá | Tồn |\n| --- | --- | --- |\n"
        "| Lốp X | 100000 | 26 |\n"
    )
    ents = _entities(md)
    assert len(ents) == 1
    assert ents[0].price_primary == 100000


def test_catalog_row_labeled_no_price_kept() -> None:
    """A named row with a REAL labelled attribute (no price) is kept — only
    pure-col_N rows are noise."""
    md = (
        "| Tên kho | Sản phẩm | Ngày |\n| --- | --- | --- |\n"
        "| Kho A | Lốp Y | 28-11 |\n"
    )
    ents = _entities(md)
    assert len(ents) == 1
    assert not any(str(k).startswith("col_") for k in (ents[0].attributes or {}))
