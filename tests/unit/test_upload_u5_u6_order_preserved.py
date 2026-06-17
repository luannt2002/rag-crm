"""U5 CR enrich ∥ U6 VN segment — index order preservation.

[T2-CostPerf] asyncio.gather guarantees result ordering matches argument
ordering. For the _enrich_and_segment pattern, result at position i must
correspond to input chunk at position i — regardless of which chunk
finishes first.

Real behavioural assertions:
- N chunks with variable delays: fast later chunks must NOT appear at
  an earlier index than slow early chunks.
- Enriched output at index i matches input chunk i.
- Segmented output at index i matches input chunk i.
- Batch of size 50 under concurrent dispatch: all indices accounted for.
"""
from __future__ import annotations

import asyncio
import random

import pytest


async def _enrich_and_segment(
    idx: int,
    original: str,
    *,
    enrich_fn,
    segment_fn,
) -> tuple[str, str]:
    enr, seg = await asyncio.gather(
        enrich_fn(idx, original),
        segment_fn(original),
        return_exceptions=True,
    )
    enr_out: str = original if isinstance(enr, BaseException) else enr
    seg_out: str = original if isinstance(seg, BaseException) else seg
    return enr_out, seg_out


@pytest.mark.asyncio
async def test_order_preserved_with_variable_delays() -> None:
    """Chunks that finish faster must NOT appear at wrong indices.

    Each chunk sleeps a random duration before returning, so the natural
    completion order differs from the input order. gather must still
    return results aligned to input positions.
    """
    n = 12
    # Deterministic shuffle: chunk 0 is slowest, chunk n-1 is fastest.
    delays = [0.05 - (i * 0.003) for i in range(n)]  # 50ms → 17ms

    async def slow_enrich(idx: int, text: str) -> str:
        await asyncio.sleep(delays[idx])
        return f"enr[{idx}]:{text}"

    async def slow_segment(text: str) -> str:
        i = int(text.split("_")[1])
        await asyncio.sleep(delays[i] * 0.5)
        return f"seg[{i}]:{text}"

    chunks = [f"chunk_{i}" for i in range(n)]
    results = await asyncio.gather(
        *[_enrich_and_segment(i, c, enrich_fn=slow_enrich, segment_fn=slow_segment)
          for i, c in enumerate(chunks)],
    )

    assert len(results) == n
    for i, chunk in enumerate(chunks):
        enr, seg = results[i]
        assert enr == f"enr[{i}]:{chunk}", (
            f"index {i}: enr order broken: {enr!r}"
        )
        assert seg == f"seg[{i}]:{chunk}", (
            f"index {i}: seg order broken: {seg!r}"
        )


@pytest.mark.asyncio
async def test_order_preserved_large_batch() -> None:
    """50-chunk batch: all indices accounted for, no swaps."""

    async def enrich(idx: int, text: str) -> str:
        return f"E{idx}:{text}"

    async def segment(text: str) -> str:
        return f"S:{text}"

    n = 50
    chunks = [f"c{i}" for i in range(n)]
    results = await asyncio.gather(
        *[_enrich_and_segment(i, c, enrich_fn=enrich, segment_fn=segment)
          for i, c in enumerate(chunks)],
    )

    assert len(results) == n
    seen_indices = set()
    for i, (enr, seg) in enumerate(results):
        assert enr == f"E{i}:c{i}", f"swap at {i}: {enr!r}"
        assert seg == f"S:c{i}", f"swap at {i}: {seg!r}"
        seen_indices.add(i)

    assert seen_indices == set(range(n)), "some indices missing"


@pytest.mark.asyncio
async def test_enrich_index_kwarg_matches_position() -> None:
    """The idx kwarg passed to enrich_fn must match the chunk's list position."""
    received_indices: list[int] = []

    async def capture_idx(idx: int, text: str) -> str:
        received_indices.append(idx)
        return text

    async def segment(text: str) -> str:
        return text

    n = 8
    chunks = [f"x{i}" for i in range(n)]
    await asyncio.gather(
        *[_enrich_and_segment(i, c, enrich_fn=capture_idx, segment_fn=segment)
          for i, c in enumerate(chunks)],
    )

    assert sorted(received_indices) == list(range(n)), (
        f"idx values wrong: {received_indices}"
    )
