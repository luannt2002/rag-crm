"""RetrievalFallbackPort contract tests.

Verifies that every shipped Strategy implements the runtime-checkable
``RetrievalFallbackPort`` Protocol so the registry's type-erased lookup
never silently returns an incompatible object.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ragbot.application.ports.retrieval_fallback_port import RetrievalFallbackPort
from ragbot.infrastructure.retrieval_fallback import (
    BM25OnlyStage2Retriever,
    HybridStage1Retriever,
    KeywordStage3Retriever,
    NullRetrievalStage,
    ParentExpandStage4Retriever,
)


@pytest.mark.parametrize(
    "cls",
    [
        HybridStage1Retriever,
        BM25OnlyStage2Retriever,
        KeywordStage3Retriever,
        ParentExpandStage4Retriever,
        NullRetrievalStage,
    ],
)
def test_strategy_is_retrieval_fallback_port(cls: type) -> None:
    instance = cls()
    # Protocol runtime check — fails if any required attribute/method is missing.
    assert isinstance(instance, RetrievalFallbackPort)
    # Required surface for observability + chain wrapper.
    assert isinstance(instance.stage_name, str) and instance.stage_name
    assert hasattr(instance, "retrieve") and callable(instance.retrieve)


@pytest.mark.asyncio
async def test_null_stage_returns_prior_result_unchanged() -> None:
    stage = NullRetrievalStage()
    prior = [{"chunk_id": "a", "score": 0.9, "content": "x"}]
    out = await stage.retrieve(
        query="anything",
        query_embedding=[0.1, 0.2],
        record_bot_id=uuid4(),
        top_k=5,
        prior_stage_result=prior,
    )
    assert out == prior
    # Aliasing safety: returned list is a copy, not the same object.
    assert out is not prior


@pytest.mark.asyncio
async def test_null_stage_empty_prior_returns_empty() -> None:
    stage = NullRetrievalStage()
    out = await stage.retrieve(
        query="q",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        prior_stage_result=None,
    )
    assert out == []


@pytest.mark.asyncio
async def test_hybrid_stage1_no_vector_store_returns_empty() -> None:
    stage = HybridStage1Retriever()
    out = await stage.retrieve(
        query="anything",
        query_embedding=[0.1, 0.2],
        record_bot_id=uuid4(),
        top_k=5,
        prior_stage_result=None,
        # No vector_store kwarg
    )
    assert out == []


@pytest.mark.asyncio
async def test_hybrid_stage1_no_embedding_returns_empty() -> None:
    class _StubVS:
        async def hybrid_search(self, **_kwargs):
            return [{"chunk_id": "x", "score": 1.0, "content": "foo"}]

    stage = HybridStage1Retriever()
    out = await stage.retrieve(
        query="anything",
        query_embedding=[],
        record_bot_id=uuid4(),
        top_k=5,
        prior_stage_result=None,
        vector_store=_StubVS(),
    )
    assert out == []


@pytest.mark.asyncio
async def test_hybrid_stage1_with_stub_vector_store_passes_kwargs() -> None:
    captured: dict = {}

    class _StubVS:
        async def hybrid_search(self, **kwargs):
            captured.update(kwargs)
            return [
                {"chunk_id": "a", "score": 0.42, "content": "alpha"},
                {"chunk_id": "b", "score": 0.21, "content": "beta"},
            ]

    bot_id = uuid4()
    stage = HybridStage1Retriever()
    out = await stage.retrieve(
        query="hello",
        query_embedding=[0.1, 0.2, 0.3],
        record_bot_id=bot_id,
        top_k=7,
        vector_store=_StubVS(),
        channel_type="web",
        embedding_column="embedding",
    )
    assert len(out) == 2
    assert out[0]["chunk_id"] == "a"
    # Wrapper forwards only kwargs in signature.
    assert captured["query_text"] == "hello"
    assert captured["query_embedding"] == [0.1, 0.2, 0.3]
    assert captured["record_bot_id"] == bot_id
    assert captured["top_k"] == 7
    assert captured["channel_type"] == "web"
    assert captured["embedding_column"] == "embedding"


@pytest.mark.asyncio
async def test_hybrid_stage1_handles_vector_store_error_gracefully() -> None:
    class _BoomVS:
        async def hybrid_search(self, **_kwargs):
            raise ValueError("backend exploded")

    stage = HybridStage1Retriever()
    out = await stage.retrieve(
        query="hello",
        query_embedding=[0.1, 0.2],
        record_bot_id=uuid4(),
        top_k=5,
        vector_store=_BoomVS(),
    )
    assert out == []
