"""Per-model token-per-minute (TPM) rate limiter — pace, don't storm.

Why: provider rate limits are per-model TPM (e.g. OpenAI 200k TPM). A cold
fan-out of ingest enrichment fires many large calls at once → 429 →
retry-with-backoff → thundering herd that wastes tokens and stalls ingest.

This limiter makes callers QUEUE instead of fire-and-429: before each LLM
call, ``acquire(model, est_tokens)`` awaits until the trailing-60s token sum
for that model would stay under the configured limit, then records the spend.
No spam, no wasted retries, no burnt tokens — requests simply pace to the cap.

Keyed by model so a cheap ingest model (nano) paces on its OWN bucket and never
throttles the live answer model (mini) — the two never contend.

Disabled (``limit_per_min <= 0``) → ``acquire`` is a no-op (zero overhead),
preserving the prior behaviour for anyone who opts out.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

import structlog

logger = structlog.get_logger(__name__)

# Smallest slice we sleep while waiting for the window to free — bounds the
# busy-wait granularity without over-sleeping past the moment budget frees.
_WAIT_SLICE_S: float = 0.25
_WINDOW_S: float = 60.0


class TpmRateLimiter:
    """Async sliding-1-minute-window token limiter, one window per model key."""

    def __init__(self, limit_per_min: int) -> None:
        self._limit = int(limit_per_min)
        # model_key → deque[(monotonic_ts, tokens)]
        self._windows: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def limit_per_min(self) -> int:
        return self._limit

    def _prune(self, key: str, now: float) -> int:
        win = self._windows[key]
        while win and (now - win[0][0]) > _WINDOW_S:
            win.popleft()
        return sum(tok for _, tok in win)

    async def acquire(self, model_key: str, est_tokens: int) -> None:
        """Block until ``est_tokens`` fits the trailing-60s budget, then record it.

        A single call larger than the whole limit is admitted once the window
        is otherwise empty (we never deadlock on an oversized request).
        """
        if self._limit <= 0:
            return
        key = model_key or "unknown"
        est = max(0, int(est_tokens))
        waited_ms = 0.0
        async with self._locks[key]:
            while True:
                now = time.monotonic()
                used = self._prune(key, now)
                if used + est <= self._limit or not self._windows[key]:
                    self._windows[key].append((now, est))
                    if waited_ms > 0:
                        logger.info(
                            "tpm_limiter_paced",
                            model=key,
                            waited_ms=round(waited_ms),
                            est_tokens=est,
                            window_used=used,
                            limit=self._limit,
                        )
                    return
                # Wait for the oldest entry to age out of the window.
                oldest_ts = self._windows[key][0][0]
                sleep_s = min(max(_WINDOW_S - (now - oldest_ts), _WAIT_SLICE_S), _WAIT_SLICE_S * 8)
                waited_ms += sleep_s * 1000
                await asyncio.sleep(sleep_s)


def estimate_request_tokens(messages: list[dict], max_output_tokens: int) -> int:
    """Cheap pre-call token estimate: ~4 chars/token for input + output cap.

    Deliberately rough — pacing only needs an order-of-magnitude figure, and
    over-estimating is the safe direction (paces slightly more conservatively).
    """
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return chars // 4 + max(0, int(max_output_tokens or 0))
