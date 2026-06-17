"""Async timing helper for perf measurement.

CLAUDE.md Rule 3 — Measure, don't guess.

Usage:
    async with timer("retrieve_chunks"):
        chunks = await retriever.fetch(...)

    # Threshold filter: only log when duration exceeds threshold
    async with timer("redis_mget", log_threshold_ms=10.0):
        cfg = await cfg_svc.get_many(keys)

Emits structlog event ``perf_timer`` with fields ``label`` and ``duration_ms``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import structlog

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def timer(label: str, *, log_threshold_ms: float = 0.0):
    """Async context manager — measure wall time of awaited block.

    Args:
        label: structured event label (e.g. ``"retrieve_chunks"``).
        log_threshold_ms: emit structlog event only when duration >= threshold.
            Default ``0.0`` = always emit.

    Notes:
        - Exception inside the wrapped block PROPAGATES; timing event still emits.
        - Duration measured via ``time.perf_counter`` (monotonic, sub-ms).
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        if ms >= log_threshold_ms:
            logger.info("perf_timer", label=label, duration_ms=round(ms, 1))
