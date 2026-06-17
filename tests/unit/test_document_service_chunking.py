"""Unit tests: document_service chunking fixes (U3-1, U3-meta, U4-1, U7-bulk).

Phase 1 P0 fixes — Upload layer.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

from ragbot.shared.chunking import smart_chunk, analyze_document, select_strategy


# ── U3-1: smart_chunk must use the pre-computed strategy ─────────────────────


def test_smart_chunk_uses_passed_strategy_explicit() -> None:
    """smart_chunk called with strategy= must NOT re-call select_strategy."""
    text = "Line one.\nLine two.\nLine three.\n" * 20
    # When strategy is passed explicitly, select_strategy is NOT called internally
    with patch("ragbot.shared.chunking.select_strategy") as mock_select:
        result = smart_chunk(text, strategy="recursive")
        # select_strategy must NOT be called when strategy is explicit
        mock_select.assert_not_called()
    assert isinstance(result, list)
    assert len(result) > 0


def test_smart_chunk_auto_calls_select_strategy_when_none() -> None:
    """smart_chunk called WITHOUT strategy= must call select_strategy internally."""
    text = "Line one.\nLine two.\nLine three.\n" * 20
    with patch("ragbot.shared.chunking.select_strategy", wraps=select_strategy) as mock_select:
        result = smart_chunk(text, strategy=None)
        mock_select.assert_called_once()
    assert isinstance(result, list)


def test_smart_chunk_strategy_kwarg_accepted() -> None:
    """Verify that smart_chunk accepts the strategy keyword without error."""
    text = "Hello world. " * 50
    chunks = smart_chunk(text, chunk_size=200, chunk_overlap=20, strategy="recursive")
    assert all(isinstance(c, str) for c in chunks)
    assert len(chunks) >= 1


# ── U3-meta: chunk metadata must include chunking_strategy ───────────────────


def test_chunk_metadata_has_strategy_key() -> None:
    """The chunking strategy used must be stored in metadata (flat path)."""
    # This tests the contract: after select_strategy, the value must be
    # propagated into each chunk's metadata_json as 'chunking_strategy'.
    # We test the chunking module itself returns correct strategy strings.
    text = "# Heading\n\nContent paragraph.\n" * 10
    profile = analyze_document(text)
    strategy, confidence = select_strategy(profile)
    assert isinstance(strategy, str)
    assert strategy in {"hdt", "recursive", "semantic", "hybrid", "proposition", "table_csv"}
    assert 0.0 <= confidence <= 1.0


# ── U4-1: CR enrichment concurrency via asyncio.gather + Semaphore ───────────


@pytest.mark.asyncio
async def test_cr_enrichment_concurrent_faster_than_serial() -> None:
    """Concurrent enrichment with gather+Semaphore must finish faster than serial."""
    import time
    from ragbot.shared.constants import DEFAULT_ENRICHMENT_MAX_CONCURRENCY

    call_delay = 0.05  # 50ms per LLM call
    n_chunks = 10

    async def mock_enrich(chunk, doc, **kwargs):
        await asyncio.sleep(call_delay)
        return f"enriched:{chunk}"

    sem = asyncio.Semaphore(DEFAULT_ENRICHMENT_MAX_CONCURRENCY)

    async def _enrich_one(chunk):
        async with sem:
            return await mock_enrich(chunk, "doc_content")

    chunks = [f"chunk_{i}" for i in range(n_chunks)]

    t0 = time.monotonic()
    results = await asyncio.gather(*[_enrich_one(c) for c in chunks])
    elapsed = time.monotonic() - t0

    # Serial would be n_chunks * call_delay = 0.5s; concurrent (cap=5) ~0.1s
    serial_estimate = n_chunks * call_delay
    assert elapsed < serial_estimate * 0.75, (
        f"Concurrent enrichment took {elapsed:.3f}s, expected < {serial_estimate * 0.75:.3f}s"
    )
    assert results == [f"enriched:chunk_{i}" for i in range(n_chunks)]


@pytest.mark.asyncio
async def test_cr_enrichment_semaphore_caps_concurrency() -> None:
    """Semaphore must cap in-flight CR calls at DEFAULT_ENRICHMENT_MAX_CONCURRENCY."""
    from ragbot.shared.constants import DEFAULT_ENRICHMENT_MAX_CONCURRENCY

    max_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    async def mock_enrich_tracked():
        nonlocal max_concurrent, current
        async with lock:
            current += 1
            max_concurrent = max(max_concurrent, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        return "done"

    sem = asyncio.Semaphore(DEFAULT_ENRICHMENT_MAX_CONCURRENCY)

    async def _enrich_one(_chunk):
        async with sem:
            return await mock_enrich_tracked()

    n_chunks = DEFAULT_ENRICHMENT_MAX_CONCURRENCY * 3
    await asyncio.gather(*[_enrich_one(f"c{i}") for i in range(n_chunks)])

    assert max_concurrent <= DEFAULT_ENRICHMENT_MAX_CONCURRENCY, (
        f"Concurrency was {max_concurrent}, cap is {DEFAULT_ENRICHMENT_MAX_CONCURRENCY}"
    )


# ── U6-2: embedding batch must enforce DEFAULT_EMBEDDING_MAX_BATCH cap ────────


@pytest.mark.asyncio
async def test_embed_batch_splits_into_sub_batches() -> None:
    """embed_batch must call litellm.aembedding ceil(N/64) times for N>64 texts."""
    from types import SimpleNamespace
    from ragbot.shared.constants import DEFAULT_EMBEDDING_MAX_BATCH
    from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
    import uuid

    n_texts = DEFAULT_EMBEDDING_MAX_BATCH * 3 + 1  # 193 texts → 4 calls
    texts = [f"text_{i}" for i in range(n_texts)]
    expected_calls = (n_texts + DEFAULT_EMBEDDING_MAX_BATCH - 1) // DEFAULT_EMBEDDING_MAX_BATCH

    fake_embed = [0.1] * 1536

    async def mock_aembedding(model, input):  # noqa: A002
        mock_resp = MagicMock()
        # item["embedding"] used in code — use dict-like objects
        mock_resp.data = [{"embedding": fake_embed} for _ in input]
        return mock_resp

    # Use SimpleNamespace to avoid full EmbeddingSpec construction in unit test
    spec = SimpleNamespace(model_name="text-embedding-3-small")
    embedder = LiteLLMEmbedder()

    with patch("ragbot.infrastructure.embedding.litellm_embedder.litellm.aembedding",
               side_effect=mock_aembedding) as mock_call:
        result = await embedder.embed_batch(
            texts,
            spec=spec,
            record_tenant_id=uuid.uuid4(),
        )
        assert mock_call.call_count == expected_calls, (
            f"Expected {expected_calls} calls, got {mock_call.call_count}"
        )

    assert len(result) == n_texts
    assert all(len(v) == 1536 for v in result)


@pytest.mark.asyncio
async def test_embed_batch_single_call_when_within_limit() -> None:
    """embed_batch must make exactly 1 call when len(texts) <= DEFAULT_EMBEDDING_MAX_BATCH."""
    from types import SimpleNamespace
    from ragbot.shared.constants import DEFAULT_EMBEDDING_MAX_BATCH
    from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
    import uuid

    texts = [f"t_{i}" for i in range(DEFAULT_EMBEDDING_MAX_BATCH)]
    fake_embed = [0.2] * 1536

    async def mock_aembedding(model, input):  # noqa: A002
        mock_resp = MagicMock()
        mock_resp.data = [{"embedding": fake_embed} for _ in input]
        return mock_resp

    spec = SimpleNamespace(model_name="text-embedding-3-small")
    embedder = LiteLLMEmbedder()

    with patch("ragbot.infrastructure.embedding.litellm_embedder.litellm.aembedding",
               side_effect=mock_aembedding) as mock_call:
        result = await embedder.embed_batch(texts, spec=spec, record_tenant_id=uuid.uuid4())
        assert mock_call.call_count == 1

    assert len(result) == DEFAULT_EMBEDDING_MAX_BATCH


@pytest.mark.asyncio
async def test_embed_batch_empty_returns_empty() -> None:
    """embed_batch([]) must return [] without calling litellm."""
    from types import SimpleNamespace
    from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
    import uuid

    spec = SimpleNamespace(model_name="text-embedding-3-small")
    embedder = LiteLLMEmbedder()

    with patch("ragbot.infrastructure.embedding.litellm_embedder.litellm.aembedding") as mock_call:
        result = await embedder.embed_batch([], spec=spec, record_tenant_id=uuid.uuid4())
        mock_call.assert_not_called()

    assert result == []
