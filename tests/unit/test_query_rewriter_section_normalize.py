"""Pin test: query path normalizes Roman → Arabic section numerals.

User types 'Chương III nói gì' but chunks store '[Chương 3 > ...]'.
The query rewriter must align the two so BM25/embedding match works.
"""
from __future__ import annotations

from ragbot.shared.chunking import normalize_vn_section_numerals


def test_normalize_idempotent_at_query_layer() -> None:
    """Apply at query path twice = same result."""
    q = "Chương III nói gì về điều 55"
    first = normalize_vn_section_numerals(q)
    second = normalize_vn_section_numerals(first)
    assert first == second == "Chương 3 nói gì về điều 55"


def test_query_normalize_roman_to_arabic_basic() -> None:
    assert normalize_vn_section_numerals("Chương III nói gì") == "Chương 3 nói gì"
    assert normalize_vn_section_numerals("Chương IV có gì") == "Chương 4 có gì"
    assert normalize_vn_section_numerals("Mục V của TT 09") == "Mục 5 của TT 09"


def test_query_normalize_no_op_on_arabic_query() -> None:
    """Already-arabic query → unchanged (idempotent at query layer)."""
    assert normalize_vn_section_numerals("Chương 3 nói gì") == "Chương 3 nói gì"
    assert normalize_vn_section_numerals("điều 55 quy định gì") == "điều 55 quy định gì"


def test_query_normalize_no_op_on_unrelated() -> None:
    """Queries without Chương|Mục|Phần markers → unchanged."""
    assert normalize_vn_section_numerals("giá triệt lông bao nhiêu") == "giá triệt lông bao nhiêu"
    assert normalize_vn_section_numerals("xin chào shop") == "xin chào shop"


def test_normalize_function_is_pure_no_state_dependency() -> None:
    """Helper must be pure (no DB / no LLM call) — safe to call in hot path."""
    # Call 1000 times rapidly — must be sub-millisecond
    import time
    t0 = time.time()
    for _ in range(1000):
        normalize_vn_section_numerals("Chương III nói gì về điều 55")
    elapsed = time.time() - t0
    assert elapsed < 1.0  # 1ms per call is the upper bound


def test_query_normalize_canonicalizes_prefix_case() -> None:
    """Prefix canonical Title-case for embedding/BM25 alignment.

    Updated 2026-05-27 hotfix: ingest stores 'Chương 3' (capital); query
    must also be 'Chương 3' for vector cosine match. Lowercase 'chương 3'
    pre-hotfix slipped through unchanged → mismatch with chunk."""
    assert normalize_vn_section_numerals("chương III nói gì") == "Chương 3 nói gì"
    assert normalize_vn_section_numerals("CHƯƠNG III") == "Chương 3"
    assert normalize_vn_section_numerals("chương 3 nói gì") == "Chương 3 nói gì"
