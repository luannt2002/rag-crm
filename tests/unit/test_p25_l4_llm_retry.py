"""P25-L4: LLM router retry + per-provider Semaphore."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.infrastructure.llm.dynamic_litellm_router import (
    DynamicLiteLLMRouter,
    _RETRYABLE_LLM_EXCEPTIONS,
)


def _make_router() -> DynamicLiteLLMRouter:
    """Minimal router with stub repo + no redis."""
    repo = AsyncMock()
    repo.list_active_models = AsyncMock(return_value=[])
    return DynamicLiteLLMRouter(ai_config_repo=repo)


def _make_cfg(provider_code: str = "openai", max_concurrent: int = 4):
    """Minimal ModelRuntimeConfig stub shape. complete_runtime reads:
    cfg.litellm_name, cfg.params.*, cfg.provider.{code,api_key,base_url,timeout_ms,max_concurrent},
    cfg.pricing.*
    """
    return SimpleNamespace(
        litellm_name="openai/gpt-4o-mini",
        params=SimpleNamespace(temperature=0.1, max_tokens=128),
        provider=SimpleNamespace(
            code=provider_code,
            api_key="sk-test",
            base_url=None,
            timeout_ms=30000,
            max_concurrent=max_concurrent,
        ),
        pricing=SimpleNamespace(
            input_per_1k_usd=Decimal("0.0001"),
            output_per_1k_usd=Decimal("0.0002"),
            cached_input_per_1k_usd=None,
        ),
    )


def _make_usage_response(text: str = "hi"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text),
            finish_reason="stop",
        )],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )


@pytest.mark.asyncio
async def test_router_retries_on_rate_limit_then_succeeds():
    """Mock litellm.acompletion raises RateLimitError twice then returns —
    retry_with_backoff should succeed within max_attempts."""
    import litellm

    router = _make_router()
    cfg = _make_cfg()

    call_count = {"n": 0}
    err = litellm.exceptions.RateLimitError("rate limited", model="x", llm_provider="openai")

    async def flaky_completion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise err
        return _make_usage_response()

    with patch("litellm.acompletion", side_effect=flaky_completion):
        result = await router.complete_runtime(cfg, [{"role": "user", "content": "hi"}])

    assert result["text"] == "hi"
    assert call_count["n"] == 3  # 2 fails + 1 success


@pytest.mark.asyncio
async def test_router_raises_llm_error_after_max_retries():
    """Mock always raises RateLimitError — should exhaust retries and raise LLMError."""
    import litellm
    from ragbot.shared.errors import LLMError

    router = _make_router()
    cfg = _make_cfg()

    err = litellm.exceptions.RateLimitError("rate limited", model="x", llm_provider="openai")

    async def always_fail(**kwargs):
        raise err

    with patch("litellm.acompletion", side_effect=always_fail):
        with pytest.raises(LLMError, match="failed after retries"):
            await router.complete_runtime(cfg, [{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_router_does_not_retry_non_retryable_exceptions():
    """Auth errors should NOT be retried — propagate on first attempt.

    BadRequestError isn't in the retryable list → retry_with_backoff passes
    it through immediately without wrapping.
    """
    import litellm

    router = _make_router()
    cfg = _make_cfg()

    call_count = {"n": 0}
    # BadRequest is NOT in _RETRYABLE_LLM_EXCEPTIONS
    err = litellm.exceptions.BadRequestError("bad prompt", model="x", llm_provider="openai")

    async def fail_with_bad_request(**kwargs):
        call_count["n"] += 1
        raise err

    with patch("litellm.acompletion", side_effect=fail_with_bad_request):
        with pytest.raises(Exception):  # BadRequestError propagates raw
            await router.complete_runtime(cfg, [{"role": "user", "content": "hi"}])

    assert call_count["n"] == 1  # no retry


@pytest.mark.asyncio
async def test_per_provider_semaphore_limits_concurrency():
    """With max_concurrent=2, 5 concurrent calls should never have >2 in flight."""
    router = _make_router()
    cfg = _make_cfg(max_concurrent=2)

    in_flight = {"n": 0, "max_seen": 0}
    lock = asyncio.Lock()

    async def slow_completion(**kwargs):
        async with lock:
            in_flight["n"] += 1
            in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["n"])
        await asyncio.sleep(0.05)
        async with lock:
            in_flight["n"] -= 1
        return _make_usage_response()

    with patch("litellm.acompletion", side_effect=slow_completion):
        await asyncio.gather(*[
            router.complete_runtime(cfg, [{"role": "user", "content": "hi"}])
            for _ in range(5)
        ])

    assert in_flight["max_seen"] <= 2, f"semaphore breached: {in_flight['max_seen']} in flight"


def test_retryable_exceptions_include_standard_set():
    """Lock the retryable-exception set — accidental removal is a regression
    (would silently make some LLM flakes bubble up as 500s instead of retry)."""
    import litellm

    required = {
        OSError,
        ConnectionError,
        TimeoutError,
        litellm.exceptions.RateLimitError,
        litellm.exceptions.ServiceUnavailableError,
        litellm.exceptions.APIConnectionError,
    }
    assert required.issubset(set(_RETRYABLE_LLM_EXCEPTIONS))


def test_provider_semaphore_lazy_init_per_provider():
    """Two different providers should get two separate Semaphores."""
    router = _make_router()
    sem_openai = router._get_semaphore("openai", 4)
    sem_anthropic = router._get_semaphore("anthropic", 8)
    assert sem_openai is not sem_anthropic
    # Same provider → same semaphore
    assert router._get_semaphore("openai", 99) is sem_openai  # capacity from first call wins


@pytest.mark.asyncio
async def test_rate_limit_does_not_trip_circuit_breaker():
    """A 429 is flow-control, NOT a provider outage (root cause 2026-06-16).

    Exhausting retries on RateLimitError must leave the per-provider breaker
    CLOSED — otherwise a cheap-tier ingest enrichment burst (nano) hitting the
    org TPM ceiling would OPEN the shared "openai" breaker and fast-fail the
    live answer model (mini), turning an upload into a chat outage.
    """
    import litellm
    from ragbot.application.services.retry_policy import CBState
    from ragbot.shared.errors import LLMError

    router = _make_router()
    cfg = _make_cfg()
    err = litellm.exceptions.RateLimitError("429", model="x", llm_provider="openai")

    async def always_429(**kwargs):
        raise err

    with patch("litellm.acompletion", side_effect=always_429):
        with pytest.raises(LLMError, match="failed after retries"):
            await router.complete_runtime(cfg, [{"role": "user", "content": "hi"}])

    cb = router._get_circuit_breaker("openai")
    assert cb.state == CBState.CLOSED, "429 must not open the breaker"
    assert cb._state.fail_count == 0, "429 must not count as a breaker failure"


@pytest.mark.asyncio
async def test_real_outage_still_trips_circuit_breaker():
    """A genuine outage (timeout / conn drop) MUST still count toward the breaker
    — the 429 carve-out must not disarm outage detection."""
    from ragbot.shared.errors import LLMError

    router = _make_router()
    cfg = _make_cfg()

    async def always_timeout(**kwargs):
        raise TimeoutError("upstream timed out")

    with patch("litellm.acompletion", side_effect=always_timeout):
        with pytest.raises(LLMError, match="failed after retries"):
            await router.complete_runtime(cfg, [{"role": "user", "content": "hi"}])

    cb = router._get_circuit_breaker("openai")
    assert cb._state.fail_count >= 1, "real outage must still trip the breaker"
