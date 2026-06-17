""" #2 — CircuitBreaker on the streaming path.

`DynamicLiteLLMRouter.complete_runtime_stream` runs a SOFT CB:

- pre-flight `can_execute()` rejects when OPEN → no LiteLLM call, no yield
- stream finishes cleanly with >=1 token → `record_success`
- stream raises mid-flight → `record_failure` + raise
- empty stream (0 tokens yielded) → `record_failure` + LLMError
- `asyncio.CancelledError` (client disconnect) → re-raise WITHOUT touching CB
- per-provider isolation preserved (one provider OPEN never poisons another)

These tests use the REAL `CircuitBreaker` instance from the router's lazy
factory (`_get_circuit_breaker`) and only mock `litellm.acompletion` so we
exercise the production state machine end-to-end.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.application.services.retry_policy import CBState
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.shared.constants import DEFAULT_CB_FAILURE_THRESHOLD
from ragbot.shared.errors import LLMError


def _make_router() -> DynamicLiteLLMRouter:
    return DynamicLiteLLMRouter(ai_config_repo=AsyncMock())


def _make_cfg(provider_code: str = "openai", litellm_name: str = "openai/gpt-4.1-mini"):
    return SimpleNamespace(
        litellm_name=litellm_name,
        params=SimpleNamespace(temperature=0.0, max_tokens=128),
        provider=SimpleNamespace(
            code=provider_code,
            api_key="sk-test",
            base_url=None,
            timeout_ms=30000,
            max_concurrent=4,
        ),
        pricing=SimpleNamespace(
            input_per_1k_usd=Decimal("0.0004"),
            output_per_1k_usd=Decimal("0.0016"),
            cached_input_per_1k_usd=Decimal("0.0001"),
        ),
    )


def _delta_chunk(text: str | None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _ExplodingStream:
    """Async iterator that raises after a fixed number of chunks."""

    def __init__(self, good_chunks, exc):
        self._chunks = list(good_chunks)
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise self._exc


@pytest.mark.asyncio
async def test_open_breaker_rejects_pre_flight_no_llm_call() -> None:
    """OPEN CB must short-circuit BEFORE any LiteLLM call or yielded token."""
    router = _make_router()
    cfg = _make_cfg(provider_code="openai")

    # Trip the breaker manually so it enters OPEN state.
    cb = router._get_circuit_breaker("openai")
    for _ in range(DEFAULT_CB_FAILURE_THRESHOLD):
        cb.record_failure()
    assert cb.state == CBState.OPEN

    acompletion_mock = AsyncMock()
    with patch("litellm.acompletion", acompletion_mock):
        with pytest.raises(LLMError, match="circuit breaker OPEN"):
            async for _ in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                pytest.fail("Should not yield any token when CB is OPEN")

    # No LiteLLM call should have been made.
    acompletion_mock.assert_not_called()


@pytest.mark.asyncio
async def test_clean_stream_records_success() -> None:
    """A stream that yields >=1 token then finishes must call record_success."""
    router = _make_router()
    cfg = _make_cfg(provider_code="openai")
    cb = router._get_circuit_breaker("openai")
    # Pre-load some failures so we can detect the reset on success.
    cb.record_failure()
    cb.record_failure()
    assert cb._state.fail_count == 2

    stream = _FakeStream([_delta_chunk("Hel"), _delta_chunk("lo")])
    with patch("litellm.acompletion", AsyncMock(return_value=stream)):
        out = [t async for t in router.complete_runtime_stream(
            cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
        )]

    assert out == ["Hel", "lo"]
    # record_success resets fail counter.
    assert cb._state.fail_count == 0
    assert cb.state == CBState.CLOSED


@pytest.mark.asyncio
async def test_mid_stream_exception_records_failure() -> None:
    """A provider 500/timeout mid-stream must call record_failure + raise."""
    router = _make_router()
    cfg = _make_cfg(provider_code="openai")
    cb = router._get_circuit_breaker("openai")
    assert cb._state.fail_count == 0

    boom = _ExplodingStream([_delta_chunk("partial ")], TimeoutError("upstream timeout"))
    with patch("litellm.acompletion", AsyncMock(return_value=boom)):
        collected: list[str] = []
        with pytest.raises(TimeoutError):
            async for tok in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                collected.append(tok)

    # Partial output must have been delivered before the failure.
    assert collected == ["partial "]
    # Provider gets penalised exactly once.
    assert cb._state.fail_count == 1


@pytest.mark.asyncio
async def test_client_cancel_does_not_record_failure() -> None:
    """asyncio.CancelledError = client disconnect; CB must stay untouched."""
    router = _make_router()
    cfg = _make_cfg(provider_code="openai")
    cb = router._get_circuit_breaker("openai")

    cancel_stream = _ExplodingStream(
        [_delta_chunk("a")], asyncio.CancelledError(),
    )
    with patch("litellm.acompletion", AsyncMock(return_value=cancel_stream)):
        with pytest.raises(asyncio.CancelledError):
            async for _ in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                pass

    # CB must NOT have been penalised — client issue, not provider.
    assert cb._state.fail_count == 0
    assert cb.state == CBState.CLOSED


@pytest.mark.asyncio
async def test_empty_stream_records_failure() -> None:
    """A stream that yields ZERO tokens is treated as upstream failure."""
    router = _make_router()
    cfg = _make_cfg(provider_code="openai")
    cb = router._get_circuit_breaker("openai")
    assert cb._state.fail_count == 0

    # Stream sends only empty / malformed chunks → 0 tokens out.
    empty_stream = _FakeStream([
        _delta_chunk(None),
        _delta_chunk(""),
        SimpleNamespace(choices=[]),
    ])
    with patch("litellm.acompletion", AsyncMock(return_value=empty_stream)):
        with pytest.raises(LLMError, match="yielded 0 tokens"):
            async for _ in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                pytest.fail("Empty stream should not yield anything")

    assert cb._state.fail_count == 1


@pytest.mark.asyncio
async def test_per_provider_isolation_streaming() -> None:
    """OpenAI flap during streaming must not poison the Anthropic breaker."""
    router = _make_router()
    cfg_openai = _make_cfg(provider_code="openai", litellm_name="openai/gpt-4.1-mini")

    # Drive OpenAI past the failure threshold using setup-time exceptions
    # (covers the "before first chunk" failure mode).
    with patch("litellm.acompletion", AsyncMock(side_effect=ConnectionError("flap"))):
        for _ in range(DEFAULT_CB_FAILURE_THRESHOLD):
            with pytest.raises(LLMError):
                async for _ in router.complete_runtime_stream(
                    cfg_openai,
                    [{"role": "user", "content": "Hi"}],
                    purpose="generation",
                ):
                    pass

    cb_openai = router._get_circuit_breaker("openai")
    cb_anthropic = router._get_circuit_breaker("anthropic")
    cb_cohere = router._get_circuit_breaker("cohere")

    assert cb_openai.state == CBState.OPEN
    assert cb_anthropic.state == CBState.CLOSED
    assert cb_cohere.state == CBState.CLOSED

    # And a fresh Anthropic stream still works.
    cfg_anthropic = _make_cfg(
        provider_code="anthropic", litellm_name="anthropic/claude-3-5",
    )
    ok_stream = _FakeStream([_delta_chunk("hi")])
    with patch("litellm.acompletion", AsyncMock(return_value=ok_stream)):
        out = [t async for t in router.complete_runtime_stream(
            cfg_anthropic,
            [{"role": "user", "content": "Hi"}],
            purpose="generation",
        )]
    assert out == ["hi"]
    assert cb_anthropic.state == CBState.CLOSED


@pytest.mark.asyncio
async def test_setup_failure_records_and_wraps_as_llm_error() -> None:
    """Failure during ``litellm.acompletion`` setup is a provider fault."""
    router = _make_router()
    cfg = _make_cfg(provider_code="openai")
    cb = router._get_circuit_breaker("openai")

    with patch("litellm.acompletion", AsyncMock(side_effect=ConnectionError("dns"))):
        with pytest.raises(LLMError, match="litellm stream failed"):
            async for _ in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                pass

    assert cb._state.fail_count == 1
