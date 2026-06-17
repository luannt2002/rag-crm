"""Bounded-concurrency (admission control) tests for embedder + reranker adapters.

Async Rule 6 / bulkhead: each external-dependency adapter caps in-flight HTTP
calls with its own semaphore so a request burst cannot self-saturate the
provider and trip the circuit breaker (the 503-collapse failure mode). These
tests fire MORE concurrent calls than the semaphore allows and assert the
observed peak concurrency never exceeds the configured cap — a real behavioural
assertion, not a structural `is not None` check.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from uuid import uuid4

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.infrastructure.embedding.zeroentropy_embedder import ZeroEntropyEmbedder
from ragbot.infrastructure.reranker.zeroentropy_reranker import ZeroEntropyReranker
from ragbot.shared.constants import (
    DEFAULT_EMBEDDER_MAX_CONCURRENT,
    DEFAULT_RERANKER_MAX_CONCURRENT,
)
from ragbot.shared.types import TenantId

_SPEC = EmbeddingSpec(
    binding_id=uuid4(), model_name="zembed-1", provider="zeroentropy",
    dimension=4, max_batch=64, model_version="1", task="passage",
)


class _ConcurrencyTracker:
    """Records peak simultaneous in-flight calls through a mocked endpoint."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.in_flight = 0
        self.peak = 0
        self._lock = asyncio.Lock()

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        # Hold the "connection" long enough that, without a semaphore, all
        # callers would overlap and drive peak == N.
        await asyncio.sleep(0.05)
        async with self._lock:
            self.in_flight -= 1
        return self._response


def _embed_response(dim: int = 4) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"results": [{"embedding": [0.1] * dim}]})
    return resp


def _rerank_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"results": [{"index": 0, "relevance_score": 0.9}]})
    return resp


@pytest.mark.asyncio
async def test_embedder_caps_in_flight_calls() -> None:
    """20 concurrent embed_one calls must never exceed the embedder semaphore."""
    emb = ZeroEntropyEmbedder(key_pool_factory=None)
    tracker = _ConcurrencyTracker(_embed_response())
    n = 20
    # Stub key resolution (pool is None) so the mocked POST is reached.
    with patch.object(
        emb, "_resolve_key", new=AsyncMock(return_value=("ze_test_key", None)),
    ), patch.object(httpx.AsyncClient, "post", new=tracker):
        await asyncio.gather(*[
            emb.embed_one(f"text {i}", spec=_SPEC, record_tenant_id=TenantId(uuid4()))
            for i in range(n)
        ])

    assert tracker.peak <= DEFAULT_EMBEDDER_MAX_CONCURRENT, (
        f"embedder peak concurrency {tracker.peak} exceeded cap "
        f"{DEFAULT_EMBEDDER_MAX_CONCURRENT}"
    )
    # Sanity: the burst really was concurrent (more than one in flight at once),
    # otherwise the cap assertion would pass trivially.
    assert tracker.peak >= 2


@pytest.mark.asyncio
async def test_reranker_caps_in_flight_calls() -> None:
    """20 concurrent rerank calls must never exceed the reranker semaphore."""
    rr = ZeroEntropyReranker(api_key="ze_test_key")
    tracker = _ConcurrencyTracker(_rerank_response())
    chunks = [{"id": "c0", "content": "doc", "score": 0.5}]
    n = 20
    with patch.object(httpx.AsyncClient, "post", new=tracker):
        await asyncio.gather(
            *[rr.rerank("query", chunks, top_n=1) for _ in range(n)]
        )

    assert tracker.peak <= DEFAULT_RERANKER_MAX_CONCURRENT, (
        f"reranker peak concurrency {tracker.peak} exceeded cap "
        f"{DEFAULT_RERANKER_MAX_CONCURRENT}"
    )
    assert tracker.peak >= 2


def test_embedder_and_reranker_have_separate_semaphores() -> None:
    """Bulkhead: the two pools are independent objects (no shared starvation)."""
    emb = ZeroEntropyEmbedder(key_pool_factory=None)
    rr = ZeroEntropyReranker(api_key="ze_test_key")
    assert emb._sem is not rr._sem
    assert emb._sem._value == DEFAULT_EMBEDDER_MAX_CONCURRENT
    assert rr._sem._value == DEFAULT_RERANKER_MAX_CONCURRENT
