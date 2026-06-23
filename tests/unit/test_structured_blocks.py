"""Unit tests — structured-markdown -> typed Block conversion (A-I5 / B-2)."""

from __future__ import annotations

from ragbot.domain.entities.document import Block
from ragbot.shared.structured_blocks import markdown_to_blocks


def test_empty_markdown_yields_no_blocks() -> None:
    assert markdown_to_blocks("") == []
    assert markdown_to_blocks("   \n\n  ") == []


def test_heading_maps_to_atomic_heading_block() -> None:
    blocks = markdown_to_blocks("# Section One\n\nSome prose body.")
    assert any(b.type == "HEADING" and b.is_atomic for b in blocks)
    heading = next(b for b in blocks if b.type == "HEADING")
    assert heading.content == "# Section One"


def test_pipe_table_maps_to_atomic_table_block() -> None:
    md = "# Prices\n\n| Name | Price |\n| --- | --- |\n| A | 100 |\n| B | 200 |"
    blocks = markdown_to_blocks(md)
    table = next(b for b in blocks if b.type == "TABLE")
    assert table.is_atomic is True
    assert "| A | 100 |" in table.content


def test_prose_maps_to_non_atomic_text_block() -> None:
    blocks = markdown_to_blocks("Just a paragraph of plain prose with no structure.")
    assert len(blocks) == 1
    assert blocks[0].type == "TEXT"
    assert blocks[0].is_atomic is False


def test_returns_at_least_one_typed_block_for_structured_doc() -> None:
    md = "# Title\n\nIntro prose.\n\n| Col |\n| --- |\n| v |"
    blocks = markdown_to_blocks(md)
    assert len(blocks) >= 1
    assert all(isinstance(b, Block) for b in blocks)
    # Heading + text + table all distinctly typed.
    types = {b.type for b in blocks}
    assert "HEADING" in types
    assert "TABLE" in types
