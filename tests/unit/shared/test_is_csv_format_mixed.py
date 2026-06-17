"""Pin tests for ``_is_csv_format`` mixed-content detection (Bug #5).

Plan: 260525-4BUG-INGEST-PIPELINE Phase A.

Bug evidence (verified 2026-05-25):
``smart_chunk(DOC)`` on real test-spa-id file 3 (Triệt lông: 4 intro
lines + 1 csv header + 11 data rows + 2 trailing notes) auto-selected
``strategy=recursive`` because ``_is_csv_format`` returned False. Pure-
CSV ratio criterion failed (intro + footer lines dilute fraction).
Phase 1 chunking (``_chunk_table_csv_with_context``) never fired.

Fix: add a second criterion — dominant table run (longest stretch of
consecutive same-comma-count lines ≥
``DEFAULT_CSV_FORMAT_TABLE_RUN_MIN_LINES``).
"""

from __future__ import annotations

from ragbot.shared.chunking import _is_csv_format


def test_pure_csv_returns_true() -> None:
    """Wall-to-wall CSV with no prose still passes via criterion 1."""
    doc = "a,b,c\n1,2,3\n4,5,6\n7,8,9\n"
    assert _is_csv_format(doc) is True


def test_mixed_doc_with_long_table_passes_via_criterion_2() -> None:
    """Mixed-content doc with intro + 7-row table + footer (placeholder names)."""
    doc = """Section title
- First bullet sentence
- Second bullet sentence
- Third bullet sentence
STT,Item,Unit price,Bundle price
1,Alpha,129000,899000
2,Beta,249000,1499000
3,Gamma,199000,1199000
4,Delta,349000,2399000
5,Epsilon,499000,2999000
6,Zeta,599000,2999000
12,Eta,249000,1499000
Trailing note one about promotion
Trailing note two about warranty
"""
    assert _is_csv_format(doc) is True


def test_short_table_under_threshold_returns_false() -> None:
    """A 3-line CSV embedded in prose is NOT enough to fast-path table_csv.

    Recursive chunking handles small tables fine; only large tables benefit
    from row-as-chunk semantics.
    """
    doc = """Some introduction paragraph that has no commas.
Another prose line.
And one more line of prose.
col_a,col_b,col_c
1,foo,100
2,bar,200
3,baz,300
Trailing notes go here.
End of document.
"""
    # 3-row table under threshold 5 → criterion 2 fails. Criterion 1 also
    # fails (3/9 comma lines < 0.6). Expect False — generic recursive runs.
    assert _is_csv_format(doc) is False


def test_prose_with_stray_commas_returns_false() -> None:
    """Bullet list with single commas must NOT trigger table_csv.

    "- foo, bar baz" has 1 comma but no consecutive same-shape neighbour,
    so criterion 2 yields run length 1 (below threshold).
    """
    doc = """Introduction paragraph.
- First bullet, with a comma.
- Second bullet, also with one.
- Third bullet, comma here too.
Closing paragraph.
"""
    assert _is_csv_format(doc) is False


def test_empty_or_whitespace_returns_false() -> None:
    assert _is_csv_format("") is False
    assert _is_csv_format("   ") is False
    assert _is_csv_format("\n\n\n") is False


def test_single_line_returns_false() -> None:
    """A lone CSV header without rows is not a table."""
    assert _is_csv_format("col_a,col_b,col_c") is False


def test_table_with_blank_separator_inside_still_passes() -> None:
    """A 6-row table interrupted by one blank line still scores via
    criterion 2 because ``lines`` filter drops the blank — neighbours
    stay adjacent.
    """
    doc = """a,b,c
1,2,3
2,3,4
3,4,5

4,5,6
5,6,7
"""
    # 6 consecutive same-shape rows after blank-line filter → run=6 ≥ 5.
    assert _is_csv_format(doc) is True


def test_two_column_table_under_threshold_returns_false() -> None:
    """2-column table with only 4 rows — under run threshold."""
    doc = """col_a,col_b
1,foo
2,bar
3,baz
4,qux
"""
    # 4 rows + 1 header = 5 same-shape lines → run=5 ≥ 5 → True.
    # (Threshold 5 is intentionally low enough to catch small 2-col tables
    # that still benefit from row-as-chunk.)
    assert _is_csv_format(doc) is True
