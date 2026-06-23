"""B-3 — block-native executor flag + string extraction parity."""

from __future__ import annotations

from ragbot.domain.entities.document import Block
from ragbot.shared.chunking import smart_chunk_atomic
from ragbot.shared.constants import DEFAULT_ATOMIC_BLOCK_CHUNKING_ENABLED


def test_atomic_block_chunking_flag_default_off() -> None:
    """Default OFF — byte-identical text path until a load-test soak flips it."""
    assert DEFAULT_ATOMIC_BLOCK_CHUNKING_ENABLED is False
    assert isinstance(DEFAULT_ATOMIC_BLOCK_CHUNKING_ENABLED, bool)


def test_u4_string_extraction_keeps_atomic_table_intact() -> None:
    """The U4 executor extracts ``original_content or narrated_text`` per Chunk.

    A TABLE block (atomic) must survive verbatim as one raw chunk string, never
    cut across rows — the spa-07 cross-row price-conflate class.
    """
    table_md = "| Name | Price |\n| --- | --- |\n| A | 100 |\n| B | 200 |"
    blocks = [
        Block(type="HEADING", content="# Prices", is_atomic=True),
        Block(type="TEXT", content="Intro prose.", is_atomic=False),
        Block(type="TABLE", content=table_md, is_atomic=True),
    ]
    chunks = smart_chunk_atomic(blocks, chunk_size=400, chunk_overlap=40)
    raw_chunks = [
        c.original_content or c.narrated_text
        for c in chunks
        if (c.original_content or c.narrated_text)
    ]
    assert raw_chunks, "executor must not zero the corpus"
    # The full table travels in exactly one chunk string.
    table_hits = [s for s in raw_chunks if "| A | 100 |" in s and "| B | 200 |" in s]
    assert len(table_hits) == 1
