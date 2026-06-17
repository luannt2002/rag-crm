"""Cross-process single-flight lock for PgSemanticCache.find_similar_with_text.

Pre-fix: ``asyncio.Lock`` keyed on (bot_id, query_hash) only serialised
coroutines INSIDE one event loop / one worker process. Two uvicorn workers
serving the same hot query both missed the cache, both computed the embedding,
both wrote a duplicate semantic_cache row.

Fix: Redis ``SET <key> 1 NX EX <ttl>`` provides a cross-process lock. The
first worker wins acquisition and performs the lookup; the second sleeps
briefly then re-enters ``find_similar_with_text`` — by which time the winner
has written the row, so the waiter hits the exact-hash fast path.

The tests below pin the contract: arguments, acquisition semantics, lock
release on completion, recursive wait-then-retry, and graceful degradation
when ``redis_client`` is ``None``.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache
from ragbot.shared.constants import (
    DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S,
)


class _NoOpSession:
    """Session that returns ``None`` for every lookup — every call is a miss."""

    async def __aenter__(self) -> "_NoOpSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        class _R:
            def mappings(self) -> Any:
                class _M:
                    def first(self) -> Any:
                        return None

                return _M()

        return _R()

    async def commit(self) -> None:
        return None


def _make_cache() -> PgSemanticCache:
    def _factory() -> _NoOpSession:
        return _NoOpSession()

    return PgSemanticCache(_factory)  # type: ignore[arg-type]


def _redis_acquire_first_only() -> MagicMock:
    """Redis double whose ``SET NX EX`` succeeds once, then fails.

    Mirrors the production race: worker A is granted the lock on the first
    ``SET NX``; every subsequent ``SET NX`` for the same key (worker B, or a
    retry from A) returns ``None`` until ``DELETE`` is issued.
    """
    redis = MagicMock()
    redis.set = AsyncMock(side_effect=[True, None, None, None])
    redis.delete = AsyncMock(return_value=1)
    return redis


# --- Contract: kwargs ------------------------------------------------------


@pytest.mark.asyncio
async def test_find_similar_with_text_accepts_redis_client_kwarg() -> None:
    """The new ``redis_client`` kwarg MUST be accepted (None is the default)."""
    cache = _make_cache()
    # No redis_client → still works (in-process fallback path).
    result = await cache.find_similar_with_text(
        query_embedding=[0.1, 0.2],
        query_text="hello",
        record_tenant_id="tenant-1",
        record_bot_id="bot-1",
        bot_version="v1",
        corpus_version="c1",
        redis_client=None,
    )
    # No-op session → guaranteed miss.
    assert result is None


# --- Contract: lock key shape ---------------------------------------------


@pytest.mark.asyncio
async def test_redis_lock_key_includes_bot_id_and_query_hash() -> None:
    """Lock key MUST be scoped to (record_bot_id, query_hash) — same shape
    as the in-process lock so two different bots / two different queries
    never collide and serialise needlessly."""
    cache = _make_cache()
    redis = _redis_acquire_first_only()

    await cache.find_similar_with_text(
        query_embedding=[0.1],
        query_text="abc",
        record_tenant_id="t1",
        record_bot_id="bot-xyz",
        bot_version="v1",
        corpus_version="c1",
        redis_client=redis,
    )

    # First positional/keyword argument to SET is the key.
    args, kwargs = redis.set.await_args_list[0]
    key = args[0] if args else kwargs.get("name") or kwargs.get("key")
    assert "bot-xyz" in key, f"lock key must scope to record_bot_id; got {key!r}"
    # query_hash includes sha256("abc") — first 12 hex chars are deterministic.
    qhash = hashlib.sha256(b"abc").hexdigest()
    assert qhash[:12] in key, (
        "lock key must include the query hash so two different queries on "
        f"the same bot do not block each other; got {key!r}"
    )


# --- Contract: SET NX EX flags --------------------------------------------


@pytest.mark.asyncio
async def test_redis_lock_uses_nx_and_ex_ttl() -> None:
    """``SET`` MUST carry ``nx=True`` (only set if absent) and ``ex=<ttl>``
    (auto-expire so a crashed holder cannot deadlock other workers)."""
    cache = _make_cache()
    redis = _redis_acquire_first_only()

    await cache.find_similar_with_text(
        query_embedding=[0.1],
        query_text="abc",
        record_tenant_id="t1",
        record_bot_id="bot-1",
        bot_version="v1",
        corpus_version="c1",
        redis_client=redis,
    )

    _, kwargs = redis.set.await_args_list[0]
    assert kwargs.get("nx") is True, "redis SET must use NX flag (single-flight)"
    assert kwargs.get("ex") == DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S, (
        "redis SET must auto-expire so a crashed holder cannot wedge other "
        f"workers; expected ex={DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S}"
    )


# --- Contract: release lock on completion ----------------------------------


@pytest.mark.asyncio
async def test_lock_holder_deletes_key_on_success() -> None:
    """After the holder finishes the lookup, ``DELETE`` MUST run so the
    next query for the same key proceeds immediately rather than waiting
    for the TTL to expire."""
    cache = _make_cache()
    redis = _redis_acquire_first_only()

    await cache.find_similar_with_text(
        query_embedding=[0.1],
        query_text="abc",
        record_tenant_id="t1",
        record_bot_id="bot-1",
        bot_version="v1",
        corpus_version="c1",
        redis_client=redis,
    )

    assert redis.delete.await_count >= 1, "lock holder must release on completion"


# --- Contract: waiter polls and retries -----------------------------------


@pytest.mark.asyncio
async def test_concurrent_misses_only_first_holds_lock(monkeypatch: Any) -> None:
    """Two concurrent ``find_similar_with_text`` calls on the same key:
    only ONE acquires the Redis lock; the other must wait + retry until
    the holder has finished (cache lookup should return ``None`` for both
    against the no-op session, but the WAITER path is exercised).
    """

    # Speed the waiter retry so the test completes inside the asyncio loop.
    monkeypatch.setattr(
        "ragbot.infrastructure.cache.semantic_cache.DEFAULT_SEMANTIC_CACHE_WAIT_RETRY_S",
        0.001,
    )

    # Coordinate timing: first SET returns True, second returns None, then
    # after the holder is "done" (delete called) the next SET succeeds again.
    acquired_seq: list[Any] = [True, None, True]

    redis = MagicMock()
    delete_called = asyncio.Event()

    async def _set(name: str, value: Any, **kwargs: Any) -> Any:
        return acquired_seq.pop(0) if acquired_seq else True

    async def _del(name: str) -> int:
        delete_called.set()
        return 1

    redis.set = AsyncMock(side_effect=_set)
    redis.delete = AsyncMock(side_effect=_del)

    cache = _make_cache()

    async def _call() -> Any:
        return await cache.find_similar_with_text(
            query_embedding=[0.1],
            query_text="same-query",
            record_tenant_id="t1",
            record_bot_id="bot-1",
            bot_version="v1",
            corpus_version="c1",
            redis_client=redis,
        )

    # Concurrent: two coroutines race for the same lock.
    results = await asyncio.gather(_call(), _call())
    assert results == [None, None], "no-op session means both must miss"

    # At least two SET attempts (winner + initial loser); waiter may retry.
    assert redis.set.await_count >= 2, (
        f"expected at least 2 SET attempts (winner + waiter); "
        f"got {redis.set.await_count}"
    )

    # The winner MUST release.
    assert delete_called.is_set(), "winner must DELETE the lock on completion"


# --- Contract: graceful degradation when redis None -----------------------


@pytest.mark.asyncio
async def test_no_redis_falls_back_to_in_process_lock() -> None:
    """When ``redis_client`` is ``None`` (e.g. Redis down / not yet wired),
    the function MUST still serve the lookup — degrades to the existing
    in-process ``asyncio.Lock`` rather than raising."""
    cache = _make_cache()
    result = await cache.find_similar_with_text(
        query_embedding=[0.1],
        query_text="abc",
        record_tenant_id="t1",
        record_bot_id="bot-1",
        bot_version="v1",
        corpus_version="c1",
        redis_client=None,
    )
    assert result is None, "no-op session forces miss; graceful degrade must not raise"


# --- Contract: redis SET raising does NOT break pipeline ------------------


@pytest.mark.asyncio
async def test_redis_set_failure_does_not_block_lookup() -> None:
    """If Redis ``SET`` raises (transient Redis outage), the lookup MUST
    still complete — ``aux dependency KHÔNG được làm chết app chính``
    (CLAUDE.md graceful-degradation rule)."""
    cache = _make_cache()
    redis = MagicMock()
    redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
    redis.delete = AsyncMock(return_value=0)

    # MUST NOT raise — falls back to in-process lock.
    result = await cache.find_similar_with_text(
        query_embedding=[0.1],
        query_text="abc",
        record_tenant_id="t1",
        record_bot_id="bot-1",
        bot_version="v1",
        corpus_version="c1",
        redis_client=redis,
    )
    assert result is None
