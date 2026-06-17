"""Tests for P16-Wave1-Phase2 retrieval fallback ladder.

Covers retry_hybrid_with_original(): the helper called when the primary
retrieve() returned zero chunks for the rewritten query. The fallback
re-runs hybrid_search with the unmodified user query.
"""

from __future__ import annotations

import pytest

from ragbot.orchestration.query_graph import retry_hybrid_with_original


class _StubVectorStore:
    """Minimal stub that accepts the same kwargs as pgvector hybrid_search."""

    def __init__(self, return_chunks: list[dict] | None = None, raise_exc: Exception | None = None):
        self._return = return_chunks or []
        self._raise = raise_exc
        self.calls: list[dict] = []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id,
        channel_type: str,
        top_k: int,
    ) -> list[dict]:
        self.calls.append({
            "query_text": query_text,
            "query_embedding": query_embedding,
            "record_bot_id": record_bot_id,
            "channel_type": channel_type,
            "top_k": top_k,
        })
        if self._raise:
            raise self._raise
        return self._return


class _StubNoHybrid:
    """vector_store without hybrid_search attribute."""


class _StubIncompatibleSig:
    """hybrid_search missing query_text param — should NOT trigger fallback."""

    async def hybrid_search(self, *, query_embedding, record_bot_id, channel_type, top_k):
        raise AssertionError("should not be called")


async def _embed_ok(query: str, state):
    return [0.1] * 8


async def _embed_empty(query: str, state):
    return []


async def _embed_raises(query: str, state):
    raise RuntimeError("embed API down")


@pytest.mark.asyncio
class TestRetryHybridWithOriginal:
    def _state(self):
        return {"record_bot_id": "bot-uuid-1", "channel_type": "web"}

    async def test_returns_chunks_on_successful_retry(self):
        """Happy path: fallback query finds chunks the rewrite missed."""
        store = _StubVectorStore(return_chunks=[{"chunk_id": "c1", "content": "x"}])
        out = await retry_hybrid_with_original(
            store, "original query", self._state(), embed_fn=_embed_ok, top_k=10,
        )
        assert len(out) == 1
        assert out[0]["chunk_id"] == "c1"
        # Original query (not rewrite) must be what went to the store
        assert store.calls[0]["query_text"] == "original query"
        assert store.calls[0]["top_k"] == 10

    async def test_returns_empty_when_store_returns_empty(self):
        """Fallback genuinely found nothing — caller stays in no-context."""
        store = _StubVectorStore(return_chunks=[])
        out = await retry_hybrid_with_original(
            store, "q", self._state(), embed_fn=_embed_ok, top_k=10,
        )
        assert out == []

    async def test_returns_empty_when_vector_store_is_none(self):
        """Don't crash when retrieval backend is absent — just [] ."""
        out = await retry_hybrid_with_original(
            None, "q", self._state(), embed_fn=_embed_ok, top_k=10,
        )
        assert out == []

    async def test_returns_empty_when_no_hybrid_search_method(self):
        """Port without hybrid_search → no fallback attempted."""
        out = await retry_hybrid_with_original(
            _StubNoHybrid(), "q", self._state(), embed_fn=_embed_ok, top_k=10,
        )
        assert out == []

    async def test_returns_empty_when_hybrid_sig_has_no_query_text(self):
        """Older port shape without query_text param must not be called —
        the fallback is a hybrid (dense + sparse) operation that needs
        the raw query string; a dense-only search can't substitute."""
        store = _StubIncompatibleSig()
        out = await retry_hybrid_with_original(
            store, "q", self._state(), embed_fn=_embed_ok, top_k=10,
        )
        assert out == []

    async def test_returns_empty_when_embedding_returns_empty(self):
        """Embedding service down / empty — fallback aborts cleanly."""
        store = _StubVectorStore(return_chunks=[{"chunk_id": "c1"}])
        out = await retry_hybrid_with_original(
            store, "q", self._state(), embed_fn=_embed_empty, top_k=10,
        )
        assert out == []
        # Store should NOT be called when there's no embedding
        assert store.calls == []

    async def test_returns_empty_when_embedding_raises(self):
        """Embed raises mid-flight — swallowed, logs, returns []."""
        store = _StubVectorStore()
        out = await retry_hybrid_with_original(
            store, "q", self._state(), embed_fn=_embed_raises, top_k=10,
        )
        assert out == []

    async def test_returns_empty_when_hybrid_search_raises(self):
        """Store raises mid-flight — swallowed, returns []."""
        store = _StubVectorStore(raise_exc=RuntimeError("DB down"))
        out = await retry_hybrid_with_original(
            store, "q", self._state(), embed_fn=_embed_ok, top_k=10,
        )
        assert out == []

    async def test_passes_through_bot_and_channel_scoping(self):
        """Tenancy isolation: bot + channel from state must reach the store."""
        state = {"record_bot_id": "bot-xyz", "channel_type": "zalo"}
        store = _StubVectorStore(return_chunks=[{"chunk_id": "c1"}])
        await retry_hybrid_with_original(
            store, "q", state, embed_fn=_embed_ok, top_k=5,
        )
        assert store.calls[0]["record_bot_id"] == "bot-xyz"
        assert store.calls[0]["channel_type"] == "zalo"
        assert store.calls[0]["top_k"] == 5

    async def test_channel_type_missing_returns_empty_no_silent_default(self):
        """3-key strict: omitting ``channel_type`` no longer silently defaults
        to ``web``. The retry helper's broad-except swallows the resulting
        ``InvariantViolation`` and returns an empty list — pinning that the
        store is NEVER called with a fabricated default channel."""
        state = {"record_bot_id": "bot-1"}  # no channel_type
        store = _StubVectorStore(return_chunks=[{"chunk_id": "c1"}])
        out = await retry_hybrid_with_original(
            store, "q", state, embed_fn=_embed_ok, top_k=10,
        )
        assert out == []
        # Critical — store MUST NOT have been invoked with a synthesised channel.
        assert store.calls == [], (
            "retry_hybrid_with_original silently defaulted channel_type — "
            "3-key violation must short-circuit, not fabricate."
        )
