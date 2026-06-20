"""Per-key TPM limiter pins (2026-06-20 — Jina 429 root-cause fix).

The `test-từ-đầu` re-ingest hit Jina ``429: 100,551/100,000 tokens per minute``.
Verified root cause: the 2 Jina keys are INDEPENDENT accounts (each 100k TPM),
the pool already round-robins, but the TPM limiter was ONE global bucket sized
``per_key × n_keys × safety`` (180k) — it admitted 180k aggregate, so under
uneven bursts one key overran its own 100k while the other idled.

Fix: each key gets its OWN ``TpmRateLimiter`` bucket sized to the per-key quota
(``tpm_per_key × safety``). Round-robin spreads load; the per-key bucket then
guarantees no single key is paced past its quota. N keys → N× headroom, enforced
independently. These pins lock that the limiter is per-key (not global) and sized
per-key (not × n_keys).
"""

from __future__ import annotations

import asyncio
import time

from ragbot.infrastructure.embedding.jina_embedder import JinaEmbedder


def test_per_key_limiters_are_distinct_buckets() -> None:
    """Two different keys → two distinct limiter instances (not one shared)."""
    emb = JinaEmbedder(tpm_per_key=100, tpm_safety_fraction=1.0)
    lim_a = asyncio.run(emb._limiter_for("keyA"))
    lim_b = asyncio.run(emb._limiter_for("keyB"))
    assert lim_a is not lim_b, "each key must get its OWN bucket, not a shared one"
    # Same key → cached same instance.
    assert asyncio.run(emb._limiter_for("keyA")) is lim_a


def test_per_key_bucket_sized_per_key_not_times_n_keys() -> None:
    """The bucket = tpm_per_key × safety — NOT × n_keys (that was the bug)."""
    emb = JinaEmbedder(tpm_per_key=100_000, tpm_safety_fraction=0.9)
    lim = asyncio.run(emb._limiter_for("k"))
    assert lim.limit_per_min == 90_000, (
        "per-key bucket must equal 100k × 0.9 = 90k; the old global bucket was "
        "100k × n_keys × 0.9 = 180k which over-admitted past a single key's quota"
    )


def test_one_key_exhausted_does_not_starve_another() -> None:
    """Filling keyA's budget must NOT block keyB — independent buckets.

    This is the property the global bucket violated: with a shared bucket,
    keyA's spend would eat keyB's headroom and the round-robin couldn't spread
    real throughput.
    """
    emb = JinaEmbedder(tpm_per_key=100, tpm_safety_fraction=1.0)  # effective 100

    async def _run() -> float:
        lim_a = await emb._limiter_for("keyA")
        lim_b = await emb._limiter_for("keyB")
        # Saturate keyA's 60s window.
        await lim_a.acquire("jina-embeddings-v3", 100)
        # keyB still has a full budget → must admit immediately (no pacing wait).
        t0 = time.monotonic()
        await lim_b.acquire("jina-embeddings-v3", 100)
        return time.monotonic() - t0

    elapsed = asyncio.run(_run())
    assert elapsed < 0.5, "keyB blocked by keyA → buckets are not independent"


def test_safety_fraction_floor_never_zero() -> None:
    """Degenerate config (tiny limit) still yields a usable (>=1) bucket."""
    emb = JinaEmbedder(tpm_per_key=1, tpm_safety_fraction=0.0001)
    lim = asyncio.run(emb._limiter_for("k"))
    assert lim.limit_per_min >= 1
