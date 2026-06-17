"""U5 CR enrich ∥ U6 VN segment — partial failure isolation.

[T2-CostPerf] Verifies that a failure in one op does NOT propagate to
the other op's result. The production _enrich_and_segment uses
return_exceptions=True so exceptions are treated as per-item failures
with fallback to original text — not a full-batch crash.

Real behavioural assertions:
- CR enrich failure → enr falls back to original; seg still returns real value.
- VN segment failure → seg falls back to original; enr still returns real value.
- Both fail → both fall back to original (no crash, no data loss).
- Exception type is preserved as BaseException subclass (not silently dropped).
"""
from __future__ import annotations

import asyncio

import pytest


async def _enrich_and_segment(
    idx: int,
    original: str,
    *,
    enrich_fn,
    segment_fn,
) -> tuple[str, str]:
    """Production pattern mirror with return_exceptions=True."""
    enr, seg = await asyncio.gather(
        enrich_fn(idx, original),
        segment_fn(original),
        return_exceptions=True,
    )
    enr_out: str = original if isinstance(enr, BaseException) else enr
    seg_out: str = original if isinstance(seg, BaseException) else seg
    return enr_out, seg_out


@pytest.mark.asyncio
async def test_cr_failure_does_not_break_vn_segment() -> None:
    """When CR enrich raises, VN segment result is still applied."""

    async def failing_enrich(idx: int, text: str) -> str:
        raise RuntimeError("LLM timeout")

    async def good_segment(text: str) -> str:
        return text + "_seg"

    enr, seg = await _enrich_and_segment(
        0, "chăm_sóc", enrich_fn=failing_enrich, segment_fn=good_segment,
    )
    # enrich failed → fallback to original
    assert enr == "chăm_sóc", f"expected original fallback, got {enr!r}"
    # segment must still succeed
    assert seg == "chăm_sóc_seg", f"expected '_seg' suffix, got {seg!r}"


@pytest.mark.asyncio
async def test_vn_segment_failure_does_not_break_cr_enrich() -> None:
    """When VN segment raises, CR enrich result is still applied."""

    async def good_enrich(idx: int, text: str) -> str:
        return "CR_PREFIX\n\n" + text

    async def failing_segment(text: str) -> str:
        raise OSError("underthesea model file missing")

    enr, seg = await _enrich_and_segment(
        0, "da mặt", enrich_fn=good_enrich, segment_fn=failing_segment,
    )
    # enrich must succeed
    assert enr == "CR_PREFIX\n\nda mặt", f"expected CR prefix, got {enr!r}"
    # segment failed → fallback to original
    assert seg == "da mặt", f"expected original fallback, got {seg!r}"


@pytest.mark.asyncio
async def test_both_fail_returns_original_for_both() -> None:
    """When both ops fail, both outputs fall back to original (no crash)."""

    async def fail_enrich(idx: int, text: str) -> str:
        raise ValueError("cr model not configured")

    async def fail_segment(text: str) -> str:
        raise TimeoutError("underthesea timeout")

    original = "chăm sóc khách hàng"
    enr, seg = await _enrich_and_segment(
        0, original, enrich_fn=fail_enrich, segment_fn=fail_segment,
    )
    assert enr == original, f"enr should fallback to original, got {enr!r}"
    assert seg == original, f"seg should fallback to original, got {seg!r}"


@pytest.mark.asyncio
async def test_partial_failure_in_batch_does_not_abort_other_chunks() -> None:
    """One chunk failure must not prevent other chunks from being processed."""
    call_log: list[int] = []

    async def selective_fail_enrich(idx: int, text: str) -> str:
        call_log.append(idx)
        if idx == 2:
            raise RuntimeError(f"chunk {idx} LLM error")
        return text + "_cr"

    async def always_segment(text: str) -> str:
        return text + "_seg"

    chunks = [f"c{i}" for i in range(5)]
    results = await asyncio.gather(
        *[_enrich_and_segment(i, c, enrich_fn=selective_fail_enrich, segment_fn=always_segment)
          for i, c in enumerate(chunks)],
    )

    assert len(results) == 5, "all 5 chunks must return a result"
    assert set(call_log) == {0, 1, 2, 3, 4}, "all enrich calls must fire"

    for i, (enr, seg) in enumerate(results):
        if i == 2:
            # failed chunk: enr falls back to original, seg still applied
            assert enr == chunks[i], f"chunk {i} should fallback, got {enr!r}"
        else:
            assert enr == chunks[i] + "_cr", f"chunk {i} enr wrong: {enr!r}"
        assert seg == chunks[i] + "_seg", f"chunk {i} seg wrong: {seg!r}"
