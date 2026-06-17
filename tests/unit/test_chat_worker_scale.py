"""P25 Phase B — chat_worker per-process concurrency contract.

The handler installed by ``chat_worker.main()`` wraps each event in an
``asyncio.Semaphore(N)`` so a single worker process never overlaps more than
``N`` pipelines. We don't spin up the whole worker (no Redis, no DB) — the
test recreates the same Semaphore-guarded pattern in isolation and asserts:

1. Concurrency is bounded by the configured limit.
2. The ``chat_worker_queue_depth`` Gauge tracks live in-flight count.
3. Configured concurrency comes from constants.py default + ``< 1`` is coerced
   to the default (defensive guard against admin misconfig).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ragbot.infrastructure.observability.metrics import chat_worker_queue_depth
from ragbot.shared.constants import DEFAULT_CHAT_WORKER_CONCURRENCY


def _coerce_concurrency(raw: int | None) -> int:
    """Mirror the guard in chat_worker.main(): non-positive → fallback."""
    if raw is None or raw < 1:
        return DEFAULT_CHAT_WORKER_CONCURRENCY
    return raw


@pytest.mark.asyncio
async def test_semaphore_caps_overlapping_handler_runs() -> None:
    in_flight = 0
    max_in_flight = 0
    n_concurrent = 4
    cap = 2

    sem = asyncio.Semaphore(cap)

    async def guarded_handler(_payload: Any) -> None:
        nonlocal in_flight, max_in_flight
        async with sem:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.05)
            finally:
                in_flight -= 1

    await asyncio.gather(*[guarded_handler({"i": i}) for i in range(n_concurrent)])
    assert max_in_flight <= cap, (
        f"Semaphore violated: peak in-flight = {max_in_flight}, cap = {cap}"
    )


@pytest.mark.asyncio
async def test_queue_depth_gauge_tracks_in_flight() -> None:
    """The Gauge MUST reach the cap then return to 0 once all coroutines exit."""
    cap = 3

    sem = asyncio.Semaphore(cap)

    # Reset to 0 so the test is deterministic regardless of prior state.
    chat_worker_queue_depth.set(0)
    starting = chat_worker_queue_depth._value.get()  # type: ignore[attr-defined]

    peak = {"v": starting}

    async def guarded_handler(_p: Any) -> None:
        async with sem:
            chat_worker_queue_depth.inc()
            try:
                cur = chat_worker_queue_depth._value.get()  # type: ignore[attr-defined]
                peak["v"] = max(peak["v"], cur)
                await asyncio.sleep(0.02)
            finally:
                chat_worker_queue_depth.dec()

    await asyncio.gather(*[guarded_handler({}) for _ in range(cap * 2)])
    final = chat_worker_queue_depth._value.get()  # type: ignore[attr-defined]
    assert peak["v"] >= cap, f"Gauge never reached cap: peak={peak['v']}, cap={cap}"
    assert final == starting, f"Gauge leaked: {final} vs starting={starting}"


def test_concurrency_coerces_invalid_value() -> None:
    assert _coerce_concurrency(None) == DEFAULT_CHAT_WORKER_CONCURRENCY
    assert _coerce_concurrency(0) == DEFAULT_CHAT_WORKER_CONCURRENCY
    assert _coerce_concurrency(-3) == DEFAULT_CHAT_WORKER_CONCURRENCY
    assert _coerce_concurrency(8) == 8


def test_default_concurrency_constant_is_positive_int() -> None:
    """Sanity: prevent regression to a magic 0/None default."""
    assert isinstance(DEFAULT_CHAT_WORKER_CONCURRENCY, int)
    assert DEFAULT_CHAT_WORKER_CONCURRENCY >= 1
