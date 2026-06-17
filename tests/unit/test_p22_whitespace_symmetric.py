"""BM25 symmetry tests — query-side compound segmentation matches ingest.

Ingest writes ``content_segmented = segment_vi_compounds(content)`` and the
trigger feeds that column into ``to_tsvector('simple', ...)``. Query side
must therefore call ``segment_vi_compounds(query_text)`` so the lexeme
streams overlap. The legacy ``tokenize_vi`` helper (lowercase + space-join)
is wrong for both paths and must not be reintroduced.
"""
from __future__ import annotations

import inspect

from ragbot.infrastructure.vector.pgvector_store import PgVectorStore
from ragbot.shared import vi_tokenizer


def test_query_path_no_word_tokenize() -> None:
    """hybrid_search must segment via segment_vi_compounds, never tokenize_vi."""
    src = inspect.getsource(PgVectorStore.hybrid_search)
    assert "tokenize_vi(query_text)" not in src, (
        "hybrid_search must not call tokenize_vi — use segment_vi_compounds "
        "to match the ingest-side content_segmented column."
    )
    assert "tokenize_vi(normalized_query)" not in src, (
        "hybrid_search must not call tokenize_vi on normalized_query."
    )
    # Symmetric segmentation: query side mirrors ingest content_segmented.
    assert "segment_vi_compounds(query_text)" in src
    assert "segment_vi_compounds(normalized_query)" in src


def test_tokenize_vi_deprecated_note() -> None:
    """tokenize_vi docstring must advertise deprecation on query path."""
    doc = inspect.getdoc(vi_tokenizer.tokenize_vi) or ""
    assert "deprecated" in doc.lower(), (
        "tokenize_vi docstring must carry a deprecation note (P22). Current doc:\n"
        f"{doc!r}"
    )
