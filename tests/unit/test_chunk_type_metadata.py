"""M10 — first-class ``chunk_type`` modality column tests.

The alembic migration lifts the modality signal out of
``metadata_json`` into a ``document_chunks.chunk_type`` column so the
retrieval path can filter modalities without parsing JSONB. The chunker
pipeline emits one of four canonical values
(``CHUNK_TYPES_ALLOWED``): ``text`` / ``table`` / ``table_row`` /
``code``.

These tests guard:

* the mapper helper (``chunk_type_for``) emits the correct label for
  prose, pipe-tables, fenced-code blocks, and the CSV row-per-chunk
  short-circuit;
* unknown / pathological inputs collapse to ``text`` (the column is
  ``NOT NULL`` so we must never emit ``None``);
* the alembic migration is wired (file present, revision IDs linked);
* the SSoT constants stay in sync with the migration's CHECK
  constraint.
"""

from __future__ import annotations

import re
from pathlib import Path

from ragbot.application.services.document_service import chunk_type_for
from ragbot.shared.constants import (
    CHUNK_TYPES_ALLOWED,
    DEFAULT_CHUNK_TYPE_CODE,
    DEFAULT_CHUNK_TYPE_TABLE,
    DEFAULT_CHUNK_TYPE_TABLE_ROW,
    DEFAULT_CHUNK_TYPE_TEXT,
)


# -----------------------------------------------------------------------------
# Classifier — happy path for each modality
# -----------------------------------------------------------------------------


def test_prose_chunk_classifies_as_text() -> None:
    assert chunk_type_for("A simple paragraph of prose.") == DEFAULT_CHUNK_TYPE_TEXT


def test_markdown_pipe_table_classifies_as_table() -> None:
    text = "| h1 | h2 |\n|----|----|\n| a  | b  |\n| c  | d  |\n"
    assert chunk_type_for(text) == DEFAULT_CHUNK_TYPE_TABLE


def test_fenced_code_block_classifies_as_code() -> None:
    text = "```python\ndef foo():\n    return 42\n```\n"
    assert chunk_type_for(text) == DEFAULT_CHUNK_TYPE_CODE


def test_csv_row_short_circuit_via_is_table_row_flag() -> None:
    """CSV row-per-chunk caller passes ``is_table_row=True`` and skips classify."""
    text = "Khoa,Số phòng,Bác sĩ\nNội,201,A"
    assert chunk_type_for(text, is_table_row=True) == DEFAULT_CHUNK_TYPE_TABLE_ROW


# -----------------------------------------------------------------------------
# Classifier — safety nets
# -----------------------------------------------------------------------------


def test_empty_input_falls_back_to_text() -> None:
    """Empty / whitespace chunks must default to TEXT (column NOT NULL)."""
    assert chunk_type_for("") == DEFAULT_CHUNK_TYPE_TEXT
    assert chunk_type_for("   \n  ") == DEFAULT_CHUNK_TYPE_TEXT


def test_image_only_chunk_collapses_to_text() -> None:
    """Modality column is tight (4 values); IMAGE / FORMULA fold to TEXT."""
    assert chunk_type_for("![alt](https://example/x.png)") == DEFAULT_CHUNK_TYPE_TEXT


def test_classifier_output_is_always_in_allowed_set() -> None:
    """Guard against future BlockType labels leaking through the mapper."""
    inputs = [
        "plain prose",
        "| h | v |\n|---|---|\n| a | b |",
        "```\ncode\n```",
        "$$\nE = mc^2\n$$",
        "",
        "![img](u)",
    ]
    for text in inputs:
        assert chunk_type_for(text) in CHUNK_TYPES_ALLOWED


# -----------------------------------------------------------------------------
# Migration wire — alembic file linked + CHECK constraint in sync
# -----------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "_archive_pre_squash_20260618"
    / "20260518_010k_chunk_type_metadata.py"
)


def test_alembic_migration_file_present() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing migration at {_MIGRATION_PATH}"


def test_alembic_migration_revisions_linked() -> None:
    body = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'revision = "010k"' in body
    assert 'down_revision = "010j"' in body


def test_migration_check_constraint_matches_constants() -> None:
    """The CHECK constraint values must mirror CHUNK_TYPES_ALLOWED exactly."""
    body = _MIGRATION_PATH.read_text(encoding="utf-8")
    # Extract the _ALLOWED_TYPES tuple from the migration source.
    match = re.search(r"_ALLOWED_TYPES\s*=\s*\(([^)]+)\)", body)
    assert match is not None, "could not locate _ALLOWED_TYPES tuple"
    raw = match.group(1)
    parsed = tuple(s.strip().strip("'\"") for s in raw.split(",") if s.strip())
    assert parsed == CHUNK_TYPES_ALLOWED


def test_constants_allowed_set_has_all_four_canonical_types() -> None:
    """Tight allowlist guard — no accidental enlargement without migration."""
    assert set(CHUNK_TYPES_ALLOWED) == {
        DEFAULT_CHUNK_TYPE_TEXT,
        DEFAULT_CHUNK_TYPE_TABLE,
        DEFAULT_CHUNK_TYPE_TABLE_ROW,
        DEFAULT_CHUNK_TYPE_CODE,
    }
