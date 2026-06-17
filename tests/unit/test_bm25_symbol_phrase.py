"""Unit tests for BM25 symbol/code-token phrase extraction (F3, plan 260604).

Root cause (load test th-03): ``websearch_to_tsquery('simple', 'range(5)')``
shatters the token into ``range & 5`` and surrounding words AND-restrict the
predicate so the code-bearing chunk never matches. ``_extract_symbol_phrase``
surfaces the raw token for an independent ``phraseto_tsquery`` OR-branch.
"""
from __future__ import annotations

from ragbot.infrastructure.vector.pgvector_store import _extract_symbol_phrase


def test_extracts_function_call_token() -> None:
    # The exact th-03 failing query.
    q = "vòng lặp for trong python với range(5) chạy mấy lần"
    assert _extract_symbol_phrase(q) == "range(5)"


def test_extracts_bare_call() -> None:
    assert _extract_symbol_phrase("print(x) làm gì") == "print(x)"


def test_no_symbol_returns_empty() -> None:
    # Plain natural-language query must NOT trigger the symbol branch.
    assert _extract_symbol_phrase("giá dịch vụ chăm sóc da chuyên sâu") == ""


def test_empty_query_returns_empty() -> None:
    assert _extract_symbol_phrase("") == ""


def test_dotted_call_token() -> None:
    assert _extract_symbol_phrase("math.sqrt(16) bằng bao nhiêu") == "math.sqrt(16)"
