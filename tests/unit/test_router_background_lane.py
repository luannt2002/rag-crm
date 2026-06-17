"""Background LLM calls run on an isolated, smaller semaphore lane.

Root cause 2026-06-13: the async grounding judge (fire-and-forget AFTER the
answer ships) shared the foreground per-provider semaphore. Under burst a
backlog of grounding calls saturated all ``DEFAULT_PROVIDER_MAX_CONCURRENT``
slots, so the NEXT turn's foreground ``generate`` queued behind them →
measured p95 24-37s while the steady-state was 3-5s.

Fix: a call carrying ``background=True`` acquires a SEPARATE provider lane
(``"{code}::background"``) capped at ``DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT``,
so post-response work can never starve the foreground request path. Selection
is by the explicit flag ONLY — the same ``purpose="grounding"`` runs both a
sync (foreground-blocking) and an async (background) path, so purpose-string
cannot decide the lane.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.shared.constants import (
    DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT,
    DEFAULT_PROVIDER_MAX_CONCURRENT,
)


def _router() -> DynamicLiteLLMRouter:
    # _get_semaphore + lane selection never touch the repo; a bare stub is enough.
    return DynamicLiteLLMRouter(ai_config_repo=object())


def test_background_cap_is_smaller_than_foreground() -> None:
    assert DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT < DEFAULT_PROVIDER_MAX_CONCURRENT
    assert DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT >= 1


def test_foreground_and_background_lanes_are_distinct_objects() -> None:
    r = _router()
    fg = r._get_semaphore("openai", DEFAULT_PROVIDER_MAX_CONCURRENT)
    bg = r._get_semaphore("openai::background", DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT)
    assert fg is not bg, "background must be a separate semaphore, not the foreground one"
    # asyncio.Semaphore exposes its remaining permits via the private _value.
    assert fg._value == DEFAULT_PROVIDER_MAX_CONCURRENT
    assert bg._value == DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT


def test_lane_selection_reads_only_the_background_flag_not_purpose() -> None:
    """Source guard: the lane is chosen by ``background`` alone.

    A sync grounding call (purpose='grounding', background=False) MUST keep the
    full foreground lane; only background=True routes to ``::background``.
    """
    src = inspect.getsource(DynamicLiteLLMRouter._complete_runtime_one)
    assert "if background:" in src, "lane must branch on the explicit background flag"
    assert "::background" in src, "background lane key must be derived from provider code"
    # Guard against a regression that reintroduces purpose-based lane selection
    # (which would wrongly throttle the SYNC grounding path).
    assert "purpose in DEFAULT_BACKGROUND" not in src
    assert "DEFAULT_BACKGROUND_LLM_PURPOSES" not in src


@pytest.mark.asyncio
async def test_background_lane_caps_concurrency_independently() -> None:
    """Behavioral: the background lane admits at most its cap concurrently,
    and doing so does NOT consume any foreground permits."""
    r = _router()
    bg = r._get_semaphore("openai::background", DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT)
    fg = r._get_semaphore("openai", DEFAULT_PROVIDER_MAX_CONCURRENT)

    live = 0
    peak = 0
    release = asyncio.Event()

    async def _bg_worker() -> None:
        nonlocal live, peak
        async with bg:
            live += 1
            peak = max(peak, live)
            await release.wait()
            live -= 1

    # Launch twice the cap; only `cap` may hold the lane at once.
    workers = [asyncio.create_task(_bg_worker()) for _ in range(DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT * 2)]
    await asyncio.sleep(0.05)

    assert peak == DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT, (
        f"background lane must cap at {DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT}, saw {peak}"
    )
    # Foreground lane is fully untouched while background is saturated.
    assert fg._value == DEFAULT_PROVIDER_MAX_CONCURRENT, (
        "saturating the background lane must not consume foreground permits"
    )

    release.set()
    await asyncio.gather(*workers)


def test_async_grounding_caller_passes_background_true() -> None:
    """The fire-and-forget grounding judge must opt into the background lane."""
    from ragbot.orchestration import query_graph

    src = inspect.getsource(query_graph._run_grounding_check_background)
    assert "background=True" in src, (
        "the async grounding judge (_run_grounding_check_background) must call "
        "llm.complete(..., background=True) so it yields to foreground generate"
    )
