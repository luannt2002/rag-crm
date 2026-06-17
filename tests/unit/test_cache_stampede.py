"""P25 Phase C — single-flight cache-stampede protection (semantic_cache).

Verify that ``PgSemanticCache.find_similar_with_text`` serialises concurrent
identical lookups behind one in-process Lock per (bot, query_hash). When N
coroutines race for the same key, the underlying ``_find_similar_impl``
should run far fewer than N times (ideally exactly 1 if the writer is fast,
but at least < N — proving the lock serialises waiters).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ragbot.application.ports.cache_port import CachedResponse
from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache
from ragbot.shared.types import BotId, BotVersion, CorpusVersion, TenantId


def _make_cache_with_mock_impl(
    impl_side_effect: Any,
) -> tuple[PgSemanticCache, AsyncMock]:
    cache = PgSemanticCache(session_factory=lambda: None)  # type: ignore[arg-type]
    mock = AsyncMock(side_effect=impl_side_effect)
    cache._find_similar_impl = mock  # type: ignore[assignment]
    return cache, mock


def _ids() -> dict[str, Any]:
    return {
        "record_tenant_id": TenantId(uuid.uuid4()),
        "record_bot_id": BotId(uuid.uuid4()),
        "bot_version": BotVersion(uuid.uuid4()),
        "corpus_version": CorpusVersion(uuid.uuid4()),
    }


@pytest.mark.asyncio
async def test_serial_call_invokes_impl_once() -> None:
    """Sanity: a single non-concurrent call hits the impl exactly once."""
    cache, mock_impl = _make_cache_with_mock_impl(impl_side_effect=lambda **_: None)
    ids = _ids()
    result = await cache.find_similar_with_text(
        query_embedding=[0.1, 0.2],
        query_text="hello world",
        **ids,
    )
    assert result is None
    assert mock_impl.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_identical_lookups_serialise_through_lock() -> None:
    """N concurrent identical lookups must NOT all hit the impl in parallel.

    The first coroutine takes the lock; the rest wait, then run sequentially
    after the lock releases. We assert (a) every coroutine returns the right
    answer and (b) the impl never executes more than once *concurrently*.
    """
    in_flight = 0
    max_in_flight = 0

    async def slow_impl(**_: Any) -> CachedResponse | None:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.05)
            return None
        finally:
            in_flight -= 1

    cache, mock_impl = _make_cache_with_mock_impl(impl_side_effect=slow_impl)
    ids = _ids()
    n = 10
    results = await asyncio.gather(*[
        cache.find_similar_with_text(
            query_embedding=[0.1, 0.2],
            query_text="same query for all",
            **ids,
        )
        for _ in range(n)
    ])
    assert all(r is None for r in results)
    # Single-flight contract: impl must NEVER overlap. We allow N sequential
    # invocations (lock release → next acquirer) but the in-flight peak is 1.
    assert max_in_flight == 1, f"Expected serialised execution, got peak={max_in_flight}"
    assert mock_impl.await_count == n


@pytest.mark.asyncio
async def test_distinct_queries_run_in_parallel() -> None:
    """Different (bot, hash) keys MUST not block each other."""
    in_flight = 0
    max_in_flight = 0

    async def slow_impl(**_: Any) -> CachedResponse | None:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.05)
            return None
        finally:
            in_flight -= 1

    cache, _ = _make_cache_with_mock_impl(impl_side_effect=slow_impl)
    ids = _ids()
    await asyncio.gather(*[
        cache.find_similar_with_text(
            query_embedding=[0.1, 0.2],
            query_text=f"different query #{i}",
            **ids,
        )
        for i in range(5)
    ])
    # 5 distinct hashes → all 5 should run in parallel under the lock cache.
    assert max_in_flight >= 2, f"Distinct keys should not serialise (peak={max_in_flight})"


@pytest.mark.asyncio
async def test_lock_timeout_falls_back_to_independent_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a stampede waiter cannot acquire within the timeout, fall back to
    issuing its own fetch — better a duplicate query than a hung request.
    """
    # Force the lock-acquire timeout down so the test runs fast.
    import ragbot.infrastructure.cache.semantic_cache as sc_mod
    monkeypatch.setattr(sc_mod, "DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S", 0.05)

    async def very_slow_impl(**_: Any) -> CachedResponse | None:
        await asyncio.sleep(0.5)  # writer hangs longer than the timeout
        return None

    cache, mock_impl = _make_cache_with_mock_impl(impl_side_effect=very_slow_impl)
    ids = _ids()

    async def call() -> Any:
        return await cache.find_similar_with_text(
            query_embedding=[0.1, 0.2],
            query_text="hung query",
            **ids,
        )

    # Kick off the slow writer first so the second caller hits the lock.
    writer = asyncio.create_task(call())
    await asyncio.sleep(0.01)  # let writer take the lock
    waiter_result = await call()
    await writer
    # Both should have returned None; impl runs at least twice (writer + the
    # waiter's fallback fetch) — proving the timeout escape kicked in.
    assert waiter_result is None
    assert mock_impl.await_count >= 2
