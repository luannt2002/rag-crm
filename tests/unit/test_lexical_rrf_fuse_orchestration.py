"""Lexical+vector RRF fuse orchestration tests.

Smoke-coverage of the integration glue without spinning up the full
LangGraph compile:

- ``_is_null_lexical`` correctly identifies the Null Object (skip).
- ``_is_null_lexical`` correctly identifies a real adapter (proceed).
- ``rrf_merge_chunks`` (the helper the orchestrator delegates to) fuses
  two non-overlapping lists into a union ordered by RRF rank.
- ``rrf_merge_chunks`` dedupes by chunk_id across vector + lexical lists
  so the downstream rerank node doesn't see duplicates.
- Empty lexical list short-circuits to the vector list unchanged
  (identity fallback — single-list path).
- Both lists empty → empty result (no crash).
"""

from __future__ import annotations

import pytest

from ragbot.application.services.multi_query_expansion import rrf_merge_chunks
from ragbot.infrastructure.retrieval.null_lexical_retrieval import NullLexicalRetrieval
from ragbot.infrastructure.retrieval.pg_bm25_retrieval import PgBM25Retrieval
from ragbot.orchestration.query_graph import _is_null_lexical


# ----- _is_null_lexical probe ----------------------------------------------


def test_is_null_lexical_recognises_null_adapter() -> None:
    assert _is_null_lexical(NullLexicalRetrieval()) is True


def test_is_null_lexical_recognises_real_adapter() -> None:
    adapter = PgBM25Retrieval(session_factory=lambda: None)
    assert _is_null_lexical(adapter) is False


def test_is_null_lexical_none_is_null() -> None:
    assert _is_null_lexical(None) is True


def test_is_null_lexical_bare_object_treated_as_real() -> None:
    # Conservative default: an unknown shape that doesn't claim "null" is
    # treated as a real adapter so the orchestrator at least tries to call
    # it. A wrong call surfaces as a logged warning, not a silent skip.
    class _Bare:
        pass
    assert _is_null_lexical(_Bare()) is False


# ----- RRF fuse semantics (the helper the retrieve node calls) -------------


def test_rrf_fuse_union_orders_by_rank_sum() -> None:
    vector = [
        {"chunk_id": "v1", "content": "vec1", "score": 0.9},
        {"chunk_id": "v2", "content": "vec2", "score": 0.7},
    ]
    lexical = [
        {"chunk_id": "L1", "content": "lex1", "score": 0.8},
        {"chunk_id": "L2", "content": "lex2", "score": 0.4},
    ]
    fused = rrf_merge_chunks([vector, lexical], rrf_k=60)
    # 4 unique chunks union (no dedupe overlap).
    assert len(fused) == 4
    assert {c["chunk_id"] for c in fused} == {"v1", "v2", "L1", "L2"}
    # Each list's rank-1 should beat its rank-2 in the fused order.
    ids = [c["chunk_id"] for c in fused]
    assert ids.index("v1") < ids.index("v2")
    assert ids.index("L1") < ids.index("L2")


def test_rrf_fuse_dedup_overlapping_chunk_ids() -> None:
    # A chunk hit by BOTH branches should accumulate score and surface as a
    # single result.
    shared = {"chunk_id": "x1", "content": "shared", "score": 0.5}
    vector = [shared, {"chunk_id": "v2", "content": "v2", "score": 0.4}]
    lexical = [shared, {"chunk_id": "L2", "content": "L2", "score": 0.3}]
    fused = rrf_merge_chunks([vector, lexical], rrf_k=60)
    ids = [c["chunk_id"] for c in fused]
    assert ids.count("x1") == 1
    # Shared chunk wins overall (top of both lists → highest RRF score).
    assert fused[0]["chunk_id"] == "x1"


def test_rrf_fuse_empty_lexical_identity_passthrough() -> None:
    vector = [
        {"chunk_id": "v1", "content": "v1", "score": 0.9},
        {"chunk_id": "v2", "content": "v2", "score": 0.7},
    ]
    fused = rrf_merge_chunks([vector, []], rrf_k=60)
    # Single non-empty list → returned unchanged (bit-exact identity).
    assert [c["chunk_id"] for c in fused] == ["v1", "v2"]
    assert fused[0]["score"] == pytest.approx(0.9)


def test_rrf_fuse_both_empty_returns_empty() -> None:
    fused = rrf_merge_chunks([[], []], rrf_k=60)
    assert fused == []


def test_rrf_fuse_no_lists_returns_empty() -> None:
    fused = rrf_merge_chunks([], rrf_k=60)
    assert fused == []


# ----- Cross-tenant guarantee (adapter-level, no orchestrator) --------------


@pytest.mark.asyncio
async def test_null_lexical_never_leaks_anything() -> None:
    # Smoke: even when called repeatedly with different bot UUIDs the Null
    # Object always returns [] — no cross-tenant leak possible.
    from uuid import uuid4
    adapter = NullLexicalRetrieval()
    bot_a = uuid4()
    bot_b = uuid4()
    assert (await adapter.search("q", bot_a, 10)) == []
    assert (await adapter.search("q", bot_b, 10)) == []
