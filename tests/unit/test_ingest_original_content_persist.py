"""P1-10 — original_content persistence (F5 dual-read producer side).

INVARIANT under test: the persist-stage helper ``_atomic_original_meta`` builds
chunk metadata that preserves the PRE-transform raw source as
``original_content`` and records shape-detected ``block_types`` — so an atomic
TABLE / FORMULA block survives verbatim in ``document_chunks.metadata`` even
after the embed-target ``content`` column was enriched + narrated.

This is the metadata-persistence side of P1-10 (the full Block->Chunk producer
swap via ``smart_chunk_atomic`` is deferred — see spec self.risk). The helper
is pure + deterministic so this exercises real behaviour with no DB / embedder
mocking.
"""
from __future__ import annotations

from ragbot.application.services.document_service.ingest_stages_store import (
    _atomic_original_meta,
)
from ragbot.shared.constants import (
    CHUNK_METADATA_KEY_BLOCK_TYPES,
    CHUNK_METADATA_KEY_ORIGINAL_CONTENT,
)


def test_table_block_original_content_persists_verbatim() -> None:
    """A pipe-table chunk must keep its exact source in ``original_content``
    and be tagged with the TABLE block type."""
    table = (
        "| STT | Item | Value |\n"
        "|---|---|---|\n"
        "| 1 | Alpha | 100 |\n"
        "| 2 | Bravo | 200 |\n"
    )
    meta = _atomic_original_meta(table)

    # original_content holds the raw table verbatim (byte-for-byte).
    assert meta[CHUNK_METADATA_KEY_ORIGINAL_CONTENT] == table
    # block_types records the shape-detected TABLE provenance (uppercase,
    # matching the Chunk entity / DEFAULT_ATOMIC_BLOCK_TYPES vocabulary).
    assert "TABLE" in meta[CHUNK_METADATA_KEY_BLOCK_TYPES]


def test_formula_block_original_content_persists_verbatim() -> None:
    """A standalone display-formula chunk must keep its exact LaTeX source in
    ``original_content`` and be tagged FORMULA."""
    formula = "$$\nE = mc^2\n$$"
    meta = _atomic_original_meta(formula)

    assert meta[CHUNK_METADATA_KEY_ORIGINAL_CONTENT] == formula
    assert "FORMULA" in meta[CHUNK_METADATA_KEY_BLOCK_TYPES]


def test_plain_text_block_tagged_text_and_preserved() -> None:
    """A prose chunk keeps original_content and is tagged TEXT (no atomic
    label invented)."""
    prose = "This is an ordinary paragraph with no atomic structure at all."
    meta = _atomic_original_meta(prose)

    assert meta[CHUNK_METADATA_KEY_ORIGINAL_CONTENT] == prose
    assert meta[CHUNK_METADATA_KEY_BLOCK_TYPES] == ["TEXT"]


def test_empty_text_yields_no_block_types() -> None:
    """Whitespace-only source must not fabricate a TEXT label and must keep
    original_content unchanged (no crash)."""
    meta = _atomic_original_meta("   ")

    assert meta[CHUNK_METADATA_KEY_ORIGINAL_CONTENT] == "   "
    assert meta[CHUNK_METADATA_KEY_BLOCK_TYPES] == []


def test_mixed_text_table_records_both_block_types() -> None:
    """A chunk that bundles a heading line above a table records BOTH TEXT and
    TABLE provenance (dedup, order-preserving)."""
    mixed = (
        "Section intro line describing the table below.\n"
        "| Col A | Col B |\n"
        "|---|---|\n"
        "| x | y |\n"
    )
    types = _atomic_original_meta(mixed)[CHUNK_METADATA_KEY_BLOCK_TYPES]

    assert "TEXT" in types
    assert "TABLE" in types
    # No duplicate labels.
    assert len(types) == len(set(types))
