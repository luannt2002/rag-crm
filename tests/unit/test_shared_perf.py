"""Unit tests for shared/perf.py async timer helper.

Covers CLAUDE.md Rule 3 contract:
- Basic timing accuracy
- Threshold filter (sub-threshold = no emit)
- Exception propagation + emit-in-finally
- Label preserved in structlog event
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from ragbot.shared.perf import timer


@pytest.mark.asyncio
async def test_timer_basic_emits_event_with_label_and_duration():
    """Sleep ~50ms → event emitted with label + duration_ms ~50."""
    with patch("ragbot.shared.perf.logger") as mock_logger:
        async with timer("test_label"):
            await asyncio.sleep(0.05)

    assert mock_logger.info.call_count == 1
    call = mock_logger.info.call_args
    assert call.args[0] == "perf_timer"
    kwargs = call.kwargs
    assert kwargs["label"] == "test_label"
    # generous bounds for CI jitter — 30..200ms
    assert 30.0 <= kwargs["duration_ms"] <= 200.0


@pytest.mark.asyncio
async def test_timer_threshold_filter_suppresses_under_threshold():
    """Sleep 5ms with threshold 100ms → no event emitted."""
    with patch("ragbot.shared.perf.logger") as mock_logger:
        async with timer("fast_op", log_threshold_ms=100.0):
            await asyncio.sleep(0.005)

    assert mock_logger.info.call_count == 0


@pytest.mark.asyncio
async def test_timer_threshold_zero_always_emits():
    """Default threshold = 0.0 → always emit even for ~0ms block."""
    with patch("ragbot.shared.perf.logger") as mock_logger:
        async with timer("instant"):
            pass

    assert mock_logger.info.call_count == 1
    assert mock_logger.info.call_args.kwargs["label"] == "instant"


@pytest.mark.asyncio
async def test_timer_exception_propagates_and_still_emits():
    """Exception inside block must propagate; timer event must still emit."""
    with patch("ragbot.shared.perf.logger") as mock_logger:
        with pytest.raises(ValueError, match="boom"):
            async with timer("error_path"):
                await asyncio.sleep(0.01)
                raise ValueError("boom")

    assert mock_logger.info.call_count == 1
    assert mock_logger.info.call_args.kwargs["label"] == "error_path"
    assert mock_logger.info.call_args.kwargs["duration_ms"] >= 0.0


@pytest.mark.asyncio
async def test_timer_duration_above_threshold_emits():
    """Sleep 50ms with threshold 10ms → event emitted."""
    with patch("ragbot.shared.perf.logger") as mock_logger:
        async with timer("slow_op", log_threshold_ms=10.0):
            await asyncio.sleep(0.05)

    assert mock_logger.info.call_count == 1
    assert mock_logger.info.call_args.kwargs["duration_ms"] >= 10.0
