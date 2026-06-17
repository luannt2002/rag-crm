"""Unit tests for the rule-based TABLE narrator (AdapChunk Layer 7).

Verifies: simple table linearisation, tail-summary on long tables,
malformed / non-table passthrough, and empty-input handling. All assertions
are behavioural (not ``assert True``).
"""
from __future__ import annotations

from ragbot.application.services.narrate.table_narrator import narrate_table
from ragbot.shared.constants import DEFAULT_TABLE_NARRATE_MAX_ROWS


def test_simple_table_two_rows() -> None:
    """A 3-col x 2-row table linearises to header + 2 row sentences."""
    md = (
        "| Name | Price | Qty |\n"
        "|------|-------|-----|\n"
        "| A    | 10    | 5   |\n"
        "| B    | 20    | 3   |"
    )
    out = narrate_table(md)

    # Header sentence lists column names in order.
    assert out.startswith("Table with 3 columns (Name, Price, Qty).")
    # Row 1 binds each header to its cell.
    assert "Row 1: Name=A, Price=10, Qty=5." in out
    assert "Row 2: Name=B, Price=20, Qty=3." in out
    # No tail summary expected on short table.
    assert "more rows" not in out


def test_minimal_table_one_row() -> None:
    """Single-row table still emits header + 1 row sentence and no tail."""
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    out = narrate_table(md)
    assert "Table with 2 columns (A, B)." in out
    assert "Row 1: A=1, B=2." in out
    assert "more rows" not in out


def test_long_table_truncates_with_tail_summary() -> None:
    """Tables longer than ``max_rows`` emit a ``(... and N more rows)`` tail."""
    total_rows = DEFAULT_TABLE_NARRATE_MAX_ROWS + 5
    lines = ["| Col1 | Col2 |", "|------|------|"]
    for i in range(total_rows):
        lines.append(f"| v{i}a | v{i}b |")
    md = "\n".join(lines)

    out = narrate_table(md)

    # First narrated row is present, last narrated row (within cap) is present.
    assert "Row 1: Col1=v0a, Col2=v0b." in out
    last_within_cap = DEFAULT_TABLE_NARRATE_MAX_ROWS
    assert (
        f"Row {last_within_cap}: "
        f"Col1=v{last_within_cap - 1}a, Col2=v{last_within_cap - 1}b."
    ) in out
    # First over-cap row must NOT appear.
    assert (
        f"Row {last_within_cap + 1}: Col1=v{last_within_cap}a"
        not in out
    )
    # Tail summary counts the suppressed rows.
    assert "(... and 5 more rows)" in out


def test_long_table_respects_custom_max_rows() -> None:
    """The ``max_rows`` keyword overrides the default cap."""
    md_lines = ["| H |", "|---|"] + [f"| r{i} |" for i in range(8)]
    md = "\n".join(md_lines)

    out = narrate_table(md, max_rows=3)

    assert "Row 1: H=r0." in out
    assert "Row 3: H=r2." in out
    assert "Row 4:" not in out  # truncated
    assert "(... and 5 more rows)" in out


def test_malformed_input_passes_through() -> None:
    """Plain prose without pipe-delimited rows is returned verbatim."""
    text = "This is just a paragraph with no table syntax at all."
    out = narrate_table(text)
    assert out == text


def test_empty_input_returns_empty_string() -> None:
    """Empty string in -> empty string out (passthrough on no rows parsed)."""
    assert narrate_table("") == ""


def test_separator_row_is_skipped() -> None:
    """Aligned (``|:---:|``) and plain (``|---|``) separator rows are filtered."""
    md = (
        "| Left | Center | Right |\n"
        "|:-----|:------:|------:|\n"
        "| a    | b      | c     |"
    )
    out = narrate_table(md)
    # Separator content must not surface as a "row".
    assert ":-----" not in out
    assert "Row 1: Left=a, Center=b, Right=c." in out
    # Only one body row -> "Row 2" must not appear.
    assert "Row 2:" not in out


def test_zip_strict_false_tolerates_ragged_rows() -> None:
    """A row with fewer cells than the header still narrates the shared prefix.

    Markdown editors sometimes produce ragged rows; we narrate what we can
    rather than crashing — embeddings on a partial row are still useful.
    """
    md = (
        "| A | B | C |\n"
        "|---|---|---|\n"
        "| 1 | 2 |"  # only 2 cells
    )
    out = narrate_table(md)
    assert "Row 1: A=1, B=2." in out
    # The missing C cell must not produce a "C=" pair.
    assert "C=" not in out
