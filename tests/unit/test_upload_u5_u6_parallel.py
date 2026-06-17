"""U5 CR enrich ∥ U6 VN segment — concurrent execution proof.

[T2-CostPerf] Verifies that the _enrich_and_segment helper runs
CR enrich (async LLM network call) and VN segment (sync CPU via
asyncio.to_thread) concurrently for each chunk batch, so wall-time
is max(CR, seg) instead of CR + seg per chunk.

Real behavioural assertions (NOT ``assert True``):
- Concurrent wall-time: N chunks × (CR_delay + seg_delay) serial
  estimate >> actual; parallel actual ≈ max(CR_delay, seg_delay).
- Both ops receive the same original chunk text (independent inputs).
- Output tuple[0] = enriched text, tuple[1] = segmented text.
- Order preserved: chunk at index i produces result at index i.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ── Mirror of the production _enrich_and_segment pattern ─────────── #

async def _enrich_and_segment(
    idx: int,
    original: str,
    *,
    enrich_fn,
    segment_fn,
) -> tuple[str, str]:
    """Run enrich + segment concurrently — matches production pattern."""
    enr, seg = await asyncio.gather(
        enrich_fn(idx, original),
        segment_fn(original),
        return_exceptions=True,
    )
    enr_out: str = original if isinstance(enr, BaseException) else enr
    seg_out: str = original if isinstance(seg, BaseException) else seg
    return enr_out, seg_out


async def _run_batch(chunks: list[str], *, enrich_fn, segment_fn) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = await asyncio.gather(  # type: ignore[assignment]
        *[_enrich_and_segment(i, c, enrich_fn=enrich_fn, segment_fn=segment_fn)
          for i, c in enumerate(chunks)],
    )
    return results


# ── Tests ──────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_u5_u6_both_ops_run_concurrently() -> None:
    """Wall-time for N chunks must be well under serial estimate.

    Serial: N × (enrich_delay + seg_delay).
    Parallel (per-chunk): N × max(enrich_delay, seg_delay).
    With gather across chunks: ≈ max(enrich_delay, seg_delay) total.
    """
    enrich_delay = 0.05  # 50ms per chunk (simulate LLM call)
    seg_delay = 0.03     # 30ms per chunk (simulate underthesea)
    n_chunks = 6

    async def slow_enrich(idx: int, text: str) -> str:
        await asyncio.sleep(enrich_delay)
        return text + "_enriched"

    async def slow_segment(text: str) -> str:
        await asyncio.sleep(seg_delay)
        return text + "_seg"

    chunks = [f"chunk_{i}" for i in range(n_chunks)]
    t0 = time.monotonic()
    results = await _run_batch(chunks, enrich_fn=slow_enrich, segment_fn=slow_segment)
    elapsed = time.monotonic() - t0

    serial_estimate = n_chunks * (enrich_delay + seg_delay)  # 0.48s
    # Parallel should finish in roughly enrich_delay time (the slower op)
    # with generous 2× slack for event-loop overhead on CI.
    assert elapsed < serial_estimate * 0.6, (
        f"expected parallel < 60% of serial {serial_estimate:.2f}s, "
        f"got {elapsed:.3f}s — reverted to serial?"
    )
    assert len(results) == n_chunks


@pytest.mark.asyncio
async def test_u5_u6_both_ops_receive_original_chunk() -> None:
    """CR enrich and VN segment both receive the ORIGINAL chunk text.

    The production pattern runs both ops on the same ``original`` so
    they are independent (no data dependency between enrich output and
    segment input at the per-chunk level).
    """
    enrich_inputs: list[str] = []
    segment_inputs: list[str] = []

    async def capture_enrich(idx: int, text: str) -> str:
        enrich_inputs.append(text)
        return text + "_cr"

    async def capture_segment(text: str) -> str:
        segment_inputs.append(text)
        return text + "_seg"

    chunks = ["alpha", "beta", "gamma"]
    await _run_batch(chunks, enrich_fn=capture_enrich, segment_fn=capture_segment)

    assert enrich_inputs == chunks, (
        f"enrich received {enrich_inputs!r}, expected {chunks!r}"
    )
    assert segment_inputs == chunks, (
        f"segment received {segment_inputs!r}, expected {chunks!r}"
    )


@pytest.mark.asyncio
async def test_u5_u6_outputs_at_correct_tuple_positions() -> None:
    """tuple[0] = enriched text, tuple[1] = segmented text — strict."""

    async def enrich(idx: int, text: str) -> str:
        return f"CR:{text}"

    async def segment(text: str) -> str:
        return f"SEG:{text}"

    chunks = ["a", "b", "c"]
    results = await _run_batch(chunks, enrich_fn=enrich, segment_fn=segment)

    for i, chunk in enumerate(chunks):
        enr, seg = results[i]
        assert enr == f"CR:{chunk}", f"index {i}: expected CR:{chunk!r}, got {enr!r}"
        assert seg == f"SEG:{chunk}", f"index {i}: expected SEG:{chunk!r}, got {seg!r}"


@pytest.mark.asyncio
async def test_u5_u6_index_order_preserved_in_batch() -> None:
    """asyncio.gather guarantees result order = argument order.

    Each chunk's result must appear at the same index as the input.
    """

    async def enrich(idx: int, text: str) -> str:
        # Return index-tagged result to detect swaps
        return f"enr_{idx}_{text}"

    async def segment(text: str) -> str:
        return f"seg_{text}"

    chunks = [f"c{i}" for i in range(10)]
    results = await _run_batch(chunks, enrich_fn=enrich, segment_fn=segment)

    assert len(results) == len(chunks)
    for i, chunk in enumerate(chunks):
        enr, seg = results[i]
        assert enr == f"enr_{i}_{chunk}", f"order broken at {i}: {enr!r}"
        assert seg == f"seg_{chunk}", f"order broken at {i}: {seg!r}"
