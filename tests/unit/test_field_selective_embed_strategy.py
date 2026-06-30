"""Field-selective embedding-text strategy — drop keyword/alias FLOOD cells from
the dense vector, keep discriminative fields. Pure-function, shape-only,
DOMAIN-NEUTRAL (no column name / bot identity). The full row stays in content +
BM25; only the embedded text is cleaned.
"""
from __future__ import annotations

from ragbot.infrastructure.embedding_text.field_selective_strategy import (
    FieldSelectiveStrategy,
    _is_flood_cell,
    _strip_flood_cells,
)
from ragbot.infrastructure.embedding_text.registry import (
    build_embedding_text_strategy,
    list_providers,
)

_S = FieldSelectiveStrategy()


def _flood(n: int) -> str:
    """A keyword swamp of *n* short separator-delimited tokens."""
    return ", ".join(f"155/80R13 var{i}" for i in range(n))


def test_registered_in_registry() -> None:
    assert "field_selective" in list_providers()
    assert build_embedding_text_strategy("field_selective").name == "field_selective"


def test_flood_cell_detected_by_shape() -> None:
    assert _is_flood_cell(_flood(40)) is True
    # short / few-part cells are NOT flood
    assert _is_flood_cell("Lốp xe LANDSPIDER 155/80R13 79T CITYTRAXX G/P") is False
    assert _is_flood_cell("684000") is False
    assert _is_flood_cell("a, b, c") is False  # few parts


def test_prose_with_commas_is_not_flood() -> None:
    # A real sentence with a handful of commas must survive (guards over-stripping).
    prose = ("Dạ, dịch vụ này giúp làm sạch, loại bỏ tế bào chết, làm trắng da, "
             "sáng mịn và đều màu hơn ạ")
    assert _is_flood_cell(prose) is False


def test_strip_flood_keeps_discriminative_cells() -> None:
    row = (
        "## Kho\n"
        "| question | code | productname | quantity | price |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| {_flood(40)} | 2-R13 155/80 LPD | Lop LANDSPIDER 155/80R13 G/P | 214 | 684000 |"
    )
    out = _S.build(raw_chunk=row, enriched_prefix=None)
    # the alias swamp is gone from the dense text...
    assert "var20" not in out
    # ...but every discriminative field stays
    assert "2-R13 155/80 LPD" in out
    assert "684000" in out and "214" in out
    assert "Lop LANDSPIDER 155/80R13 G/P" in out
    # header row (short labels) untouched
    assert "| question | code | productname | quantity | price |" in out


def test_domain_neutral_any_column_name() -> None:
    """The flood column can be named ANYTHING (or be a different domain) — the
    strip is by SHAPE, not by the header 'question'/'aliases'."""
    # A medical/legal-flavoured table with the swamp under a 'synonyms' column.
    row = (
        "| name | synonyms | dose |\n"
        "| --- | --- | --- |\n"
        f"| Paracetamol | {_flood(30)} | 500mg |"
    )
    out = _S.build(raw_chunk=row, enriched_prefix=None)
    assert "var15" not in out          # swamp stripped regardless of column name
    assert "Paracetamol" in out and "500mg" in out


def test_non_table_chunk_is_byte_identical() -> None:
    prose = "Điều 3 quy định về phân loại hệ thống thông tin theo cấp độ."
    assert _S.build(raw_chunk=prose, enriched_prefix=None) == prose


def test_table_without_flood_is_byte_identical_to_prefix_raw() -> None:
    """No flood cell → behaves exactly like prefix_plus_raw (safe default)."""
    row = "| name | price |\n| --- | --- |\n| Massage body | 600000 |"
    assert _strip_flood_cells(row) == row  # unchanged
    assert _S.build(raw_chunk=row, enriched_prefix=None) == row


def test_prefix_prepended_when_present() -> None:
    row = "| name | price |\n| --- | --- |\n| X | 1 |"
    out = _S.build(raw_chunk=row, enriched_prefix="Context: price sheet")
    assert out == f"Context: price sheet\n\n{row}"
