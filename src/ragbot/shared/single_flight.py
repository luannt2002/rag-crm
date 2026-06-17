"""Async single-flight helper — coalesce concurrent cache misses.

When N coroutines race for the same cache key on a cold cache, the naive
fast-path "miss → DB query" issues N parallel DB queries. Single-flight
solves this by serialising waiters behind one in-process ``asyncio.Lock``
keyed by the cache key: the first coroutine takes the lock and queries the
backing store, subsequent coroutines await the lock and re-check the cache,
which the first writer is expected to have populated.

This module provides a generic helper used by ``BotRegistryService``,
``TenantConfigCache``, and the RBAC middleware. The semantic_cache layer
keeps its own bespoke implementation because it predates this helper and
already ships in production with its own test coverage; reusing the
helper there is a follow-up refactor.

Pattern reference:
    src/ragbot/infrastructure/cache/semantic_cache.py — proven
    in-process single-flight (P25 Phase C, see test_cache_stampede.py).

Memory: locks accumulate until the bound is reached, then a best-effort
LRU eviction drops the oldest unlocked entries. Eviction is bounded by
``DEFAULT_SINGLE_FLIGHT_MAX_LOCKS`` so a high-tenancy worker does not
grow the dict unboundedly. Locked entries are never evicted (a waiter is
queued on them) — they age out naturally once the writer finishes.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Optional

import structlog

from ragbot.shared.constants import (
    DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S,
    DEFAULT_SINGLE_FLIGHT_MAX_LOCKS,
)

logger = structlog.get_logger(__name__)

try:  # pragma: no cover — optional metrics import (tests may not load app)
    from ragbot.infrastructure.observability.metrics import (
        cache_stampede_avoided_total,
    )
except Exception:  # noqa: BLE001 — optional metrics dep, fall back to no-op when prometheus_client absent
    cache_stampede_avoided_total = None  # type: ignore[assignment]


class AsyncSingleFlight:
    """Per-key ``asyncio.Lock`` registry with bounded LRU eviction.

    Used by callers that want to coalesce concurrent misses on the same
    key into one backing-store fetch. The helper is **only the lock
    registry** — coalescing logic (double-check pattern + write) lives
    in the caller because the DB query and cache write differ per cache.

    Parameters
    ----------
    cache_label:
        Prometheus ``cache_name`` label value emitted on the
        ``ragbot_cache_stampede_avoided_total`` counter every time a
        waiter has to wait for an in-flight lock. Picking a stable
        low-cardinality label (e.g. "bot_registry", "tenant_config",
        "rbac") keeps the counter cheap.
    max_locks:
        Soft cap. Once the lock dict reaches this size, the LRU eviction
        sweep drops the oldest unlocked entries until the dict size is
        back under the cap. Caller-tunable via constants; default value
        is ``DEFAULT_SINGLE_FLIGHT_MAX_LOCKS``.
    lock_timeout_s:
        Used by ``wait_for_lock_release`` — caps how long a waiter
        blocks on the in-flight lock before falling back to its own
        fetch. A duplicate query is preferable to a hung request.
    """

    def __init__(
        self,
        cache_label: str,
        *,
        max_locks: int = DEFAULT_SINGLE_FLIGHT_MAX_LOCKS,
        lock_timeout_s: float = DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S,
    ) -> None:
        if not cache_label:
            raise ValueError("cache_label is required for stampede metric labelling")
        if max_locks <= 0:
            raise ValueError("max_locks must be positive")
        self._cache_label = cache_label
        self._max_locks = int(max_locks)
        self._lock_timeout_s = float(lock_timeout_s)
        # OrderedDict gives us O(1) move-to-end on access for an LRU sweep.
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        # Mutex for the locks-dict structure itself (we don't want two
        # coroutines racing to insert the same key + getting two locks).
        self._registry_mutex = asyncio.Lock()

    @property
    def cache_label(self) -> str:
        return self._cache_label

    def __len__(self) -> int:
        return len(self._locks)

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Return (lazy-create) the per-key ``asyncio.Lock`` for ``key``.

        Inserts under ``_registry_mutex`` so two concurrent misses for
        the same key see the same lock. After insert/access the entry
        is moved to the OrderedDict tail so the LRU sweep favours
        evicting cold keys first.
        """
        async with self._registry_mutex:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
                # Soft eviction: only sweep when we exceed the cap.
                if len(self._locks) > self._max_locks:
                    self._evict_unlocked()
            else:
                # LRU bump — fresh access wins over stale ones at eviction time.
                self._locks.move_to_end(key)
            return lock

    def _evict_unlocked(self) -> None:
        """Best-effort LRU sweep — drop oldest unlocked entries.

        Locked entries stay (a writer holds it; evicting it would orphan
        the waiter). The sweep walks oldest-first and stops once the
        dict is back at or under the cap, so the worst-case cost is one
        scan across the entire dict in the rare burst-overflow case.
        """
        # Snapshot so we can mutate the dict mid-iteration safely.
        for key in list(self._locks.keys()):
            if len(self._locks) <= self._max_locks:
                return
            lock = self._locks[key]
            if not lock.locked():
                # No waiter is queued on it; safe to drop.
                del self._locks[key]

    async def wait_for_lock_release(self, lock: asyncio.Lock) -> bool:
        """Block on a held lock; record the stampede avoid metric.

        Returns ``True`` when the lock was acquired within the timeout
        (caller should re-check the cache then drop the lock), ``False``
        when the wait timed out (caller should fall back to issuing its
        own fetch — better a duplicate query than a hung request).

        Increments ``cache_stampede_avoided_total{cache=<label>}`` every
        time the metric is enabled and the wait actually had to queue.
        """
        self._record_stampede_avoided()
        try:
            await asyncio.wait_for(
                lock.acquire(),
                timeout=self._lock_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "single_flight_wait_timeout",
                cache=self._cache_label,
                timeout_s=self._lock_timeout_s,
            )
            return False
        return True

    def _record_stampede_avoided(self) -> None:
        if cache_stampede_avoided_total is None:
            return
        try:
            cache_stampede_avoided_total.labels(cache_name=self._cache_label).inc()
        except Exception:  # noqa: BLE001 — prometheus client may raise on shutdown; metrics never break pipeline
            pass


__all__ = ["AsyncSingleFlight"]
