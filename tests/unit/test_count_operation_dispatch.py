"""[T1-Smartness] B-AGG Phase 1 — a COUNT question must carry ``operation="count"``
so the stats dispatcher can run a COUNT aggregate instead of collapsing into the
``keyword`` row-return path (which dumps priced rows → the LLM fabricates a count
or leaks a price). Shape-only, domain-neutral, multi-bot.

RED before fix: ``parse_list_query`` hardcodes ``operation="keyword"`` for every
list/count/category hit (query_range_parser.py:353), so a "có bao nhiêu" question
is indistinguishable from a "liệt kê" question and both return rows.
"""
from __future__ import annotations

from ragbot.shared.query_range_parser import parse_list_query


def test_count_question_carries_count_operation_not_keyword() -> None:
    # "có bao nhiêu" folds to "co bao nhieu" → contains the vi count signal
    # "bao nhieu"; the residual keyword "Davanti" survives the strip (len>=2).
    rf = parse_list_query("có bao nhiêu sản phẩm Davanti")
    assert rf is not None, "a count question must produce a stats RangeFilter"
    assert rf.operation == "count", (
        "a 'how many' question must carry operation='count' so the dispatcher "
        "runs a COUNT aggregate, not the keyword row-dump that lets the LLM "
        "fabricate a count / leak a price (B-AGG)"
    )


def test_pure_list_question_stays_enumerate_not_count() -> None:
    # "liệt kê" is enumerate, NOT count — it must keep returning records so the
    # LLM can list them; only an explicit count signal flips to 'count'.
    rf = parse_list_query("liệt kê sản phẩm Davanti")
    assert rf is not None
    assert rf.operation != "count", (
        "a pure list/enumerate question must NOT be treated as a count"
    )
