"""Single-flight lock pool in PgSemanticCache must not leak.

Pre-fix: ``self._inflight_locks: dict[str, asyncio.Lock]`` grew once per
unique ``(record_bot_id, query_hash)`` and was never reaped. A process
serving N distinct queries over its lifetime accumulated N locks — a
slow memory leak the bigger the bot fleet got.

Fix: WeakValueDictionary so an entry is garbage-collected when the last
strong reference (held by an active critical section) drops.
"""

from __future__ import annotations

import asyncio
import gc
import weakref
from typing import Any

import pytest

from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache


class _NoOpSession:
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


def test_inflight_locks_pool_is_weakvaluedictionary() -> None:
    """Confirm the pool is the weak-ref-backed dict, not a plain dict."""
    cache = _make_cache()
    assert isinstance(cache._inflight_locks, weakref.WeakValueDictionary), (
        "lock pool MUST be WeakValueDictionary to bound memory; "
        f"got {type(cache._inflight_locks).__name__}"
    )


def test_inflight_lock_garbage_collected_when_unreferenced() -> None:
    """After all strong references drop, the pool entry MUST disappear.

    This is the load-bearing invariant: unused locks must not leak.
    """
    cache = _make_cache()
    key = "bot-x:hash-y"

    # Create one lock, drop the strong reference immediately.
    lock = cache._get_inflight_lock(key)
    assert key in cache._inflight_locks
    assert lock is not None
    del lock
    # WeakValueDictionary may need a GC pass to clear stale entries.
    gc.collect()

    assert key not in cache._inflight_locks, (
        "lock entry must be reaped once no strong reference remains; "
        f"pool still holds {list(cache._inflight_locks)}"
    )


def test_inflight_lock_survives_active_critical_section() -> None:
    """While ``async with lock`` is in flight, the entry MUST stay alive."""

    async def _run() -> tuple[bool, bool]:
        cache = _make_cache()
        key = "bot-a:hash-b"
        lock = cache._get_inflight_lock(key)
        in_pool_before = key in cache._inflight_locks
        async with lock:
            gc.collect()
            in_pool_during = key in cache._inflight_locks
        return in_pool_before, in_pool_during

    before, during = asyncio.run(_run())
    assert before is True, "lock must register in pool after first creation"
    assert during is True, (
        "lock entry MUST survive while a coroutine holds it — premature "
        "reap would let two coroutines race the same key"
    )


def test_inflight_pool_bound_by_live_working_set() -> None:
    """Many unique keys, each with no surviving caller, must collapse the pool.

    Pre-fix this was N unique keys → N locks forever; post-fix the pool
    size after GC should be close to zero (transient holds may remain).
    """
    cache = _make_cache()
    # Touch many distinct keys without retaining any strong references.
    for i in range(200):
        _ = cache._get_inflight_lock(f"bot:{i}")
    gc.collect()
    # Allow a tiny slack for any in-flight references the test harness
    # itself may hold; the important invariant is <<200 (not equal).
    leaked = len(cache._inflight_locks)
    assert leaked < 20, (
        f"WeakValueDictionary should have reaped most entries; got {leaked} "
        f"remaining out of 200 created (expected near 0)."
    )
