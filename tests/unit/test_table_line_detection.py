"""Table-line detection (L2 block-detect) — header-like branch must not swallow prose.

Control-register item E: the `_is_table_line` "header-like" branch
(``^[A-ZÀ-Ỹa-zà-ỹ\\s]+[|]``) matched ANY prose line containing a single pipe, so a
happy-case prose sentence with one ``|`` was misclassified as a table block. A real
header/table row has ≥2 pipes (≥3 cells). Fixtures are domain-neutral.
"""
from __future__ import annotations

from ragbot.shared.chunking.analyze import _is_table_line


def test_prose_with_single_pipe_is_not_table():
    """A prose line with ONE pipe must NOT be classified as a table row (item E)."""
    assert _is_table_line("Giá trị tốt | đảm bảo chất lượng cao nhất") is False


def test_header_no_leading_pipe_two_pipes_is_table():
    """A genuine header without leading pipe but ≥2 pipes is still a table row."""
    assert _is_table_line("Cột A | Cột B | Cột C") is True


def test_happy_case_markdown_pipe_table_is_table():
    """Happy-case markdown table (leading+trailing pipe) stays a table row."""
    assert _is_table_line("| Cột A | Cột B |") is True


def test_separator_line_is_table():
    assert _is_table_line("| --- | --- |") is True
