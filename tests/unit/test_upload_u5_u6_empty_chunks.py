"""U5 CR enrich ∥ U6 VN segment — edge cases (empty / single chunk).

[T2-CostPerf] Verifies that the _enrich_and_segment pattern handles
degenerate inputs without crashing or producing wrong results.

Real behavioural assertions:
- 0 chunks: gather over empty iterable returns []; no ops fired.
- 1 chunk: single-item gather still dispatches both ops.
- Chunk is empty string: both ops receive "" and return their output.
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
    enr, seg = await asyncio.gather(
        enrich_fn(idx, original),
        segment_fn(original),
        return_exceptions=True,
    )
    enr_out: str = original if isinstance(enr, BaseException) else enr
    seg_out: str = original if isinstance(seg, BaseException) else seg
    return enr_out, seg_out


async def _run_batch(chunks, *, enrich_fn, segment_fn):
    return await asyncio.gather(
        *[_enrich_and_segment(i, c, enrich_fn=enrich_fn, segment_fn=segment_fn)
          for i, c in enumerate(chunks)],
    )


@pytest.mark.asyncio
async def test_empty_chunk_list_returns_empty_results() -> None:
    """Zero-chunk batch: gather on empty iterable → empty list, no fn calls."""
    calls: list[str] = []

    async def enrich(idx: int, text: str) -> str:
        calls.append(f"enrich:{idx}")
        return text + "_cr"

    async def segment(text: str) -> str:
        calls.append(f"seg:{text}")
        return text + "_seg"

    results = await _run_batch([], enrich_fn=enrich, segment_fn=segment)

    assert list(results) == [], f"expected empty result from gather, got {results!r}"
    assert calls == [], f"no ops should fire on empty input, got {calls!r}"


@pytest.mark.asyncio
async def test_single_chunk_dispatches_both_ops() -> None:
    """Single-chunk batch: both enrich and segment must fire exactly once."""
    enrich_calls: list[int] = []
    segment_calls: list[str] = []

    async def enrich(idx: int, text: str) -> str:
        enrich_calls.append(idx)
        return "CR:" + text

    async def segment(text: str) -> str:
        segment_calls.append(text)
        return "SEG:" + text

    results = await _run_batch(["hello"], enrich_fn=enrich, segment_fn=segment)

    assert len(results) == 1
    enr, seg = results[0]
    assert enr == "CR:hello"
    assert seg == "SEG:hello"
    assert enrich_calls == [0], f"enrich must fire once at idx=0: {enrich_calls}"
    assert segment_calls == ["hello"], f"segment must fire once: {segment_calls}"


@pytest.mark.asyncio
async def test_empty_string_chunk_does_not_crash() -> None:
    """Empty-string chunk: both ops receive '' and must return some string."""

    async def enrich(idx: int, text: str) -> str:
        return "PREFIX\n\n" + text  # prefix + empty = "PREFIX\n\n"

    async def segment(text: str) -> str:
        return text  # identity on empty string

    results = await _run_batch([""], enrich_fn=enrich, segment_fn=segment)
    assert len(results) == 1
    enr, seg = results[0]
    assert isinstance(enr, str), f"enr must be str, got {type(enr)}"
    assert isinstance(seg, str), f"seg must be str, got {type(seg)}"
    assert enr == "PREFIX\n\n", f"unexpected enr: {enr!r}"
    assert seg == "", f"identity segment of '' should be '', got {seg!r}"


@pytest.mark.asyncio
async def test_whitespace_only_chunk_handled_gracefully() -> None:
    """Whitespace-only chunk (often produced by bad chunkers): no crash."""

    async def enrich(idx: int, text: str) -> str:
        return text.strip() or "[empty]"

    async def segment(text: str) -> str:
        return text  # underthesea returns whitespace unchanged

    results = await _run_batch(["   \n  "], enrich_fn=enrich, segment_fn=segment)
    assert len(results) == 1
    enr, seg = results[0]
    assert enr == "[empty]"
    assert seg == "   \n  "
