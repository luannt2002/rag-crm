"""Individual stage strategy tests — each stage gets its own scenarios.

Coverage:
- BM25OnlyStage2: returns chunks from a mocked session_factory; empty
  query short-circuits; missing record_bot_id short-circuits.
- KeywordStage3: regex-anchor query triggers SQL; non-matching query
  returns []; configured score stays below early-exit threshold.
- ParentExpandStage4: appends parent chunks to prior result; no
  prior result -> []; no parent_chunk_id in prior -> pass-through.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from ragbot.infrastructure.retrieval_fallback import (
    BM25OnlyStage2Retriever,
    KeywordStage3Retriever,
    ParentExpandStage4Retriever,
)
from ragbot.shared.constants import (
    DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD,
    DEFAULT_RETRIEVAL_KEYWORD_STAGE_SCORE,
)


# ----- Shared fake session_factory ----------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self):
        class _M:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _M(self._rows)

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]] | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._rows = rows or []
        self._raise = raise_exc
        self.last_sql: str | None = None
        self.last_params: dict | None = None

    async def execute(self, statement, params=None):  # noqa: ANN001
        self.last_sql = str(statement)
        self.last_params = params
        if self._raise:
            raise self._raise
        return _FakeResult(self._rows)


def _make_session_factory(rows: list[dict[str, Any]] | None = None,
                          raise_exc: Exception | None = None,
                          sink: list | None = None):
    """Return an async-context-manager session_factory matching SQLAlchemy shape."""

    @asynccontextmanager
    async def _cm():
        session = _FakeSession(rows=rows, raise_exc=raise_exc)
        if sink is not None:
            sink.append(session)
        try:
            yield session
        finally:
            pass

    def _factory():
        return _cm()

    return _factory


# ----- BM25-only stage 2 ---------------------------------------------------


@pytest.mark.asyncio
async def test_bm25_only_stage2_returns_chunks_from_mocked_session() -> None:
    rows = [
        {
            "id": "chunk-1",
            "record_document_id": "doc-1",
            "chunk_index": 0,
            "content": "Điều 8 quy định về xử phạt",
            "metadata_json": {"src": "law.pdf"},
            "score": 0.7,
        },
        {
            "id": "chunk-2",
            "record_document_id": "doc-2",
            "chunk_index": 3,
            "content": "Khoản 1 Điều 8 chi tiết",
            "metadata_json": None,
            "score": 0.5,
        },
    ]
    sf = _make_session_factory(rows=rows)
    stage = BM25OnlyStage2Retriever()
    out = await stage.retrieve(
        query="Điều 8 là gì",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=10,
        session_factory=sf,
    )
    assert len(out) == 2
    assert out[0]["chunk_id"] == "chunk-1"
    assert out[0]["score"] == pytest.approx(0.7)
    assert out[0]["stage"] == "bm25_only_stage2"
    # content + text aliases for downstream nodes.
    assert out[0]["content"] == out[0]["text"]
    assert out[1]["metadata"] == {}


@pytest.mark.asyncio
async def test_bm25_only_stage2_empty_query_returns_empty() -> None:
    sf = _make_session_factory(rows=[{"id": "x"}])
    stage = BM25OnlyStage2Retriever()
    out = await stage.retrieve(
        query="   ",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        session_factory=sf,
    )
    assert out == []


@pytest.mark.asyncio
async def test_bm25_only_stage2_no_session_factory_returns_empty() -> None:
    stage = BM25OnlyStage2Retriever()
    out = await stage.retrieve(
        query="hello",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
    )
    assert out == []


@pytest.mark.asyncio
async def test_bm25_only_stage2_db_error_returns_empty_not_crash() -> None:
    sf = _make_session_factory(raise_exc=ValueError("db down"))
    stage = BM25OnlyStage2Retriever()
    out = await stage.retrieve(
        query="hello",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        session_factory=sf,
    )
    assert out == []


# ----- Keyword stage 3 -----------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_stage3_no_anchor_in_query_returns_empty() -> None:
    sf = _make_session_factory(rows=[{"id": "irrelevant"}])
    stage = KeywordStage3Retriever()
    out = await stage.retrieve(
        query="hello world",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        session_factory=sf,
    )
    assert out == []


@pytest.mark.asyncio
async def test_keyword_stage3_with_anchor_runs_query_and_scores_below_threshold() -> None:
    rows = [
        {
            "id": "c-1",
            "record_document_id": "d-1",
            "chunk_index": 0,
            "content": "Điều 8 quy định nghĩa vụ",
            "metadata_json": {"law": "civil"},
        },
    ]
    sink: list = []
    sf = _make_session_factory(rows=rows, sink=sink)
    stage = KeywordStage3Retriever()
    out = await stage.retrieve(
        query="Điều 8 là gì",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        session_factory=sf,
    )
    assert len(out) == 1
    assert out[0]["anchor"].lower().startswith("điều 8")
    # Score is intentionally BELOW early-exit threshold so the chain
    # continues past stage 3 for reranking/grounding.
    assert out[0]["score"] == DEFAULT_RETRIEVAL_KEYWORD_STAGE_SCORE
    assert out[0]["score"] < DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD
    # Anchor was passed to LIKE.
    assert "like_pat" in (sink[0].last_params or {})


@pytest.mark.asyncio
async def test_keyword_stage3_runtime_pattern_override_works() -> None:
    rows = [{"id": "c-1", "record_document_id": "d-1", "chunk_index": 0,
             "content": "SKU-123 in stock", "metadata_json": {}}]
    sf = _make_session_factory(rows=rows)
    stage = KeywordStage3Retriever()
    out = await stage.retrieve(
        query="What is SKU-123 status",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        session_factory=sf,
        keyword_pattern=r"SKU-\d+",
    )
    assert len(out) == 1
    assert out[0]["anchor"] == "SKU-123"


# ----- Parent expand stage 4 ----------------------------------------------


@pytest.mark.asyncio
async def test_parent_expand_stage4_no_prior_result_returns_empty() -> None:
    sf = _make_session_factory(rows=[{"id": "x"}])
    stage = ParentExpandStage4Retriever()
    out = await stage.retrieve(
        query="q",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        prior_stage_result=None,
        session_factory=sf,
    )
    assert out == []


@pytest.mark.asyncio
async def test_parent_expand_stage4_no_parent_links_passes_through() -> None:
    prior = [{"chunk_id": "child-1", "score": 0.2, "content": "child text"}]
    sf = _make_session_factory(rows=[])
    stage = ParentExpandStage4Retriever()
    out = await stage.retrieve(
        query="q",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        prior_stage_result=prior,
        session_factory=sf,
    )
    # Same chunks back (parent_chunk_id absent in prior).
    assert len(out) == 1
    assert out[0]["chunk_id"] == "child-1"


@pytest.mark.asyncio
async def test_parent_expand_stage4_appends_parent_chunks() -> None:
    prior = [
        {
            "chunk_id": "child-1",
            "parent_chunk_id": "parent-1",
            "score": 0.30,
            "content": "child",
        },
        {
            "chunk_id": "child-2",
            "parent_chunk_id": "parent-2",
            "score": 0.28,
            "content": "child2",
        },
    ]
    rows = [
        {"id": "parent-1", "record_document_id": "d-1", "chunk_index": 0,
         "content": "PARENT 1 full text", "metadata_json": {}},
        {"id": "parent-2", "record_document_id": "d-2", "chunk_index": 1,
         "content": "PARENT 2 full text", "metadata_json": {}},
    ]
    sf = _make_session_factory(rows=rows)
    stage = ParentExpandStage4Retriever()
    out = await stage.retrieve(
        query="q",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=10,
        prior_stage_result=prior,
        session_factory=sf,
    )
    chunk_ids = [c["chunk_id"] for c in out]
    # Children kept + parents appended.
    assert "child-1" in chunk_ids
    assert "child-2" in chunk_ids
    assert "parent-1" in chunk_ids
    assert "parent-2" in chunk_ids
    # Parents tagged.
    parent_rows = [c for c in out if c["chunk_id"].startswith("parent-")]
    assert all(p.get("is_parent_expanded") for p in parent_rows)
    assert all(p.get("stage") == "parent_expand_stage4" for p in parent_rows)
    # Parent inherits the max prior score (0.30) so the chain doesn't
    # falsely early-exit on the parent alone.
    assert all(p["score"] == pytest.approx(0.30) for p in parent_rows)


@pytest.mark.asyncio
async def test_parent_expand_stage4_caps_to_top_k() -> None:
    prior = [
        {"chunk_id": f"c{i}", "parent_chunk_id": f"p{i}", "score": 0.2,
         "content": f"c{i}"} for i in range(5)
    ]
    rows = [
        {"id": f"p{i}", "record_document_id": "d", "chunk_index": i,
         "content": f"p{i} full", "metadata_json": {}} for i in range(5)
    ]
    sf = _make_session_factory(rows=rows)
    stage = ParentExpandStage4Retriever()
    out = await stage.retrieve(
        query="q",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=4,
        prior_stage_result=prior,
        session_factory=sf,
    )
    assert len(out) == 4  # capped


@pytest.mark.asyncio
async def test_parent_expand_stage4_dedup_parents_query() -> None:
    """Two children sharing the same parent_chunk_id must dedupe before SQL."""
    prior = [
        {"chunk_id": "c1", "parent_chunk_id": "shared-parent", "score": 0.2,
         "content": "c1"},
        {"chunk_id": "c2", "parent_chunk_id": "shared-parent", "score": 0.2,
         "content": "c2"},
    ]
    rows = [{"id": "shared-parent", "record_document_id": "d",
             "chunk_index": 0, "content": "parent", "metadata_json": {}}]
    sink: list = []
    sf = _make_session_factory(rows=rows, sink=sink)
    stage = ParentExpandStage4Retriever()
    out = await stage.retrieve(
        query="q",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=10,
        prior_stage_result=prior,
        session_factory=sf,
    )
    # The dedup happens before SQL; only one parent ID sent.
    sent_ids = (sink[0].last_params or {}).get("ids", [])
    assert len(sent_ids) == 1
    assert sent_ids[0] == "shared-parent"
    # And the parent appears exactly once in output.
    parents = [c for c in out if c["chunk_id"] == "shared-parent"]
    assert len(parents) == 1
