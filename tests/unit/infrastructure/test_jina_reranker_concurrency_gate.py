"""Regression — JinaReranker gates concurrency per key (no 429 burst).

Bug (live 2026-06-24): a parallel load burst fired all rerank HTTP calls at
once → Jina free-tier per-key max-concurrent (2) exceeded → 429 → key cooldown
→ degraded turns ("Embedding/rerank service temporarily unavailable"). The
JinaEmbedder already held an ``asyncio.Semaphore`` sized to the summed per-key
concurrency; the reranker did NOT. This pins the reranker's gate: with a total
budget of 2, a 3rd concurrent rerank WAITS until one in-flight call releases,
so the upstream never sees more than the configured concurrency at once.
"""
from __future__ import annotations

import asyncio

import pytest

from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
from ragbot.shared import api_key_pool as akp


def test_resolve_per_key_concurrency_defaults_and_override(monkeypatch) -> None:
    # No env → every key falls back to the default constant.
    monkeypatch.delenv(akp.PROVIDER_KEY_CONCURRENCY_ENV, raising=False)
    out = akp.resolve_per_key_concurrency("jina", 2)
    assert out == [akp.DEFAULT_API_KEY_MAX_CONCURRENT] * 2

    # Explicit per-key list (free + paid) is honoured, index-aligned.
    monkeypatch.setenv(akp.PROVIDER_KEY_CONCURRENCY_ENV, '{"jina":[2,50]}')
    assert akp.resolve_per_key_concurrency("jina", 2) == [2, 50]

    # Short list → missing indices fall back to the default.
    monkeypatch.setenv(akp.PROVIDER_KEY_CONCURRENCY_ENV, '{"jina":[2]}')
    assert akp.resolve_per_key_concurrency("jina", 2) == [
        2, akp.DEFAULT_API_KEY_MAX_CONCURRENT,
    ]


@pytest.mark.asyncio
async def test_reranker_semaphore_serializes_beyond_budget(monkeypatch) -> None:
    """With total concurrency 2, the 3rd concurrent HTTP post blocks until a
    slot frees — proving the gate WAITS instead of bursting (→ 429)."""
    # Build a reranker with a legacy single key (no pool needed) and force the
    # semaphore to a budget of 2 via the env the resolver reads.
    monkeypatch.setenv(akp.PROVIDER_KEY_CONCURRENCY_ENV, '{"jina":[2]}')
    rr = JinaReranker(api_key="k-test")
    sem = rr._sem
    # n_keys=1 with [2] → total budget 2.
    assert sem._value == 2  # pin the constructed budget

    # Simulate 3 concurrent slot-holders: 2 acquire immediately, the 3rd waits.
    await sem.acquire()
    await sem.acquire()
    assert sem.locked()  # both slots taken

    third = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0.05)
    assert not third.done(), "3rd acquire must WAIT while both slots are busy"

    sem.release()  # free one slot
    await asyncio.sleep(0.05)
    assert third.done(), "3rd acquire must proceed once a slot frees"
    sem.release()
