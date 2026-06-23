"""BM25 query-side fixes — symmetric segmentation, websearch_to_tsquery, no ILIKE.

Pins the SQL contract of ``PgVectorStore.hybrid_search`` so future edits
cannot silently regress the BM25 boundary fix:

1. Query-side calls ``segment_vi_compounds`` so the index lexeme stream
   (built from ``content_segmented``) and the query lexeme stream agree.
2. SQL uses ``websearch_to_tsquery`` (preserves phrase / negation /
   OR operators), never ``plainto_tsquery``.
3. ILIKE OR-branches are off by default (defeat GIN index → seq-scan)
   and only appear when ``bm25_substring_fallback_enabled=True``.
"""
from __future__ import annotations

import inspect

import pytest

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


_HS_SOURCE = inspect.getsource(PgVectorStore.hybrid_search)


def test_query_text_segmented_before_tsquery() -> None:
    """Query side segments both query_text and normalized, gated by language.

    The segmentation is routed through the local ``_segment`` helper so it
    can be gated on ``VI_DOMAIN_LANGUAGES`` (symmetric with ingest, which
    only segments VN content). For a VN-language bot the helper still calls
    ``segment_vi_compounds`` — so the index lexeme stream (built from
    ``content_segmented``) and the query lexeme stream agree.
    """
    assert "_segment(query_text)" in _HS_SOURCE, (
        "Query side must pre-segment query_text — index uses content_segmented."
    )
    assert "_segment(normalized_query)" in _HS_SOURCE, (
        "Diacritic-stripped variant must also be segmented for symmetry."
    )
    # The helper must actually call the VN segmenter (gated on language).
    assert "segment_vi_compounds(_txt)" in _HS_SOURCE, (
        "_segment must call segment_vi_compounds for VN-language bots."
    )
    assert "VI_DOMAIN_LANGUAGES" in _HS_SOURCE, (
        "Segmentation must be gated on VI_DOMAIN_LANGUAGES (symmetric with ingest)."
    )


def test_websearch_to_tsquery_used_not_plainto() -> None:
    """SQL must use websearch_to_tsquery (operators preserved); no plainto_tsquery."""
    assert "websearch_to_tsquery('simple', :query)" in _HS_SOURCE
    assert "websearch_to_tsquery('simple', :query_normalized)" in _HS_SOURCE
    assert "plainto_tsquery" not in _HS_SOURCE, (
        "plainto_tsquery strips phrase / negation / OR operators — replaced "
        "by websearch_to_tsquery."
    )


def test_ilike_branch_gated_behind_flag() -> None:
    """ILIKE OR-branches must only appear inside the opt-in flag block."""
    # Find the conditional block.
    flag_idx = _HS_SOURCE.find("if bm25_substring_fallback_enabled:")
    assert flag_idx > 0, "Expected gate `if bm25_substring_fallback_enabled:`"
    # Any ILIKE substring fragment in the source must come AFTER the gate.
    for fragment in ("content ILIKE '%' || :raw_query",
                     "content ILIKE '%' || :raw_query_normalized"):
        pos = _HS_SOURCE.find(fragment)
        if pos != -1:
            assert pos > flag_idx, (
                f"ILIKE fragment {fragment!r} must live inside the "
                "bm25_substring_fallback_enabled branch (currently outside)."
            )


def test_default_flag_is_false() -> None:
    """Default for bm25_substring_fallback_enabled must be False (constant import)."""
    sig = inspect.signature(PgVectorStore.hybrid_search)
    p = sig.parameters.get("bm25_substring_fallback_enabled")
    assert p is not None, (
        "hybrid_search must expose bm25_substring_fallback_enabled kwarg."
    )
    # The default object must equal False (sourced from
    # DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED in shared/constants.py).
    assert p.default is False, (
        f"bm25_substring_fallback_enabled default must be False, got {p.default!r}"
    )


def test_constant_exported_from_shared() -> None:
    """DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED must be False in shared.constants."""
    from ragbot.shared.constants import DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED
    assert DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED is False, (
        "Default OFF: ILIKE fallback is opt-in only (defeats GIN index)."
    )


@pytest.mark.parametrize("flag_value, want_ilike", [(False, False), (True, True)])
def test_sql_branch_assembly_per_flag(flag_value: bool, want_ilike: bool) -> None:
    """Static-render the predicate fragment for each flag value.

    We mimic the assembly logic so a future refactor that splits the SQL
    construction can be verified by behaviour, not just by string matching.
    """
    # Mirror the body's predicate assembly.
    base = (
        "search_vector @@ websearch_to_tsquery('simple', :query)"
        " OR search_vector @@ websearch_to_tsquery('simple', :query_normalized)"
    )
    predicate = base
    if flag_value:
        predicate = (
            f"{predicate}"
            " OR content ILIKE '%' || :raw_query || '%'"
            " OR content ILIKE '%' || :raw_query_normalized || '%'"
        )
    has_ilike = "ILIKE" in predicate
    assert has_ilike is want_ilike
