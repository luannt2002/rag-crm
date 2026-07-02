"""[Deep-debug OBS-F6] Streaming cost fallback parity with the sync path.

Some upstream proxies (the innocom gateway) omit the streaming ``usage`` chunk, so
both token totals drain to 0 and streamed generation logs $0 — unmeasurable, and it
is the HOTTEST call path. The router now tiktoken-estimates the missing count from
the prompt messages + the accumulated answer text (parity with the sync path). A
REAL provider usage payload is never overwritten (only a 0 is filled).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter


def _make_router() -> DynamicLiteLLMRouter:
    return DynamicLiteLLMRouter(ai_config_repo=AsyncMock())


def _make_cfg(provider_code: str = "openai", litellm_name: str = "openai/gpt-4.1-mini"):
    return SimpleNamespace(
        litellm_name=litellm_name,
        params=SimpleNamespace(temperature=0.0, max_tokens=128),
        provider=SimpleNamespace(
            code=provider_code, api_key="sk-test", base_url=None,
            timeout_ms=30000, max_concurrent=4,
        ),
        pricing=SimpleNamespace(
            input_per_1k_usd=Decimal("0.0004"),
            output_per_1k_usd=Decimal("0.0016"),
            cached_input_per_1k_usd=Decimal("0.0001"),
        ),
    )


def _delta_chunk(text: str | None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def _usage_chunk(prompt: int, completion: int):
    """A final chunk carrying a real provider usage payload (no delta)."""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
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


async def _drain(router, cfg, messages, sink):
    async for _ in router.complete_runtime_stream(
        cfg, messages, purpose="generation", usage_sink=sink,
    ):
        pass


@pytest.mark.asyncio
async def test_stream_without_usage_estimates_tokens_and_cost():
    """No usage chunk → the sink still records non-zero tokens + cost (tiktoken)."""
    router = _make_router()
    cfg = _make_cfg()
    captured: dict = {}

    def sink(prompt, completion, cached, cost, finish):
        captured.update(
            prompt=prompt, completion=completion, cached=cached, cost=cost, finish=finish,
        )

    stream = _FakeStream([
        _delta_chunk("Phòng deluxe "),
        _delta_chunk("giá 1.500.000đ "),
        _delta_chunk("một đêm."),
    ])  # NOTE: no usage chunk — mimics the innocom gateway
    messages = [
        {"role": "system", "content": "Bạn là trợ lý khách sạn."},
        {"role": "user", "content": "giá phòng deluxe bao nhiêu?"},
    ]

    with patch("litellm.acompletion", AsyncMock(return_value=stream)):
        await _drain(router, cfg, messages, sink)

    assert captured, "usage_sink was never called"
    assert captured["prompt"] > 0, "prompt tokens must be estimated, not 0"
    assert captured["completion"] > 0, "completion tokens must be estimated, not 0"
    assert captured["cost"] > 0.0, "cost must be non-zero once tokens are estimated"


@pytest.mark.asyncio
async def test_stream_with_real_usage_is_not_overwritten():
    """A real provider usage payload must win — the fallback fills only a 0."""
    router = _make_router()
    cfg = _make_cfg()
    captured: dict = {}

    def sink(prompt, completion, cached, cost, finish):
        captured.update(prompt=prompt, completion=completion)

    stream = _FakeStream([
        _delta_chunk("hi"),
        _usage_chunk(prompt=1234, completion=56),  # real provider counts
    ])
    messages = [{"role": "user", "content": "Hi"}]

    with patch("litellm.acompletion", AsyncMock(return_value=stream)):
        await _drain(router, cfg, messages, sink)

    assert captured["prompt"] == 1234, "real prompt count must not be overwritten"
    assert captured["completion"] == 56, "real completion count must not be overwritten"
