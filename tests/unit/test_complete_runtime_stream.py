"""Real-token LLM streaming via DynamicLiteLLMRouter.complete_runtime_stream.

Verifies:
- The streaming variant yields per-token deltas as they arrive from
  ``litellm.acompletion(stream=True)``.
- Anthropic ``cache_control`` is applied to the system prompt before the
  upstream call (parity with the non-streaming path).
- Empty deltas are skipped, missing ``choices`` shapes are tolerated.
- Upstream failure surfaces as ``LLMError`` (no retry, no partial silence).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
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
    """Build a fake LiteLLM streaming chunk."""
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


@pytest.mark.asyncio
async def test_complete_runtime_stream_yields_token_deltas():
    """Each non-empty delta is yielded as-is, in order."""
    router = _make_router()
    cfg = _make_cfg()
    stream = _FakeStream([
        _delta_chunk("Hel"),
        _delta_chunk("lo "),
        _delta_chunk("world"),
    ])

    with patch("litellm.acompletion", AsyncMock(return_value=stream)):
        out = []
        async for token in router.complete_runtime_stream(
            cfg,
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "Hi"},
            ],
            purpose="generation",
        ):
            out.append(token)

    assert out == ["Hel", "lo ", "world"]
    assert "".join(out) == "Hello world"


@pytest.mark.asyncio
async def test_complete_runtime_stream_skips_empty_and_malformed_chunks():
    """Empty deltas and chunks missing ``choices`` are tolerated."""
    router = _make_router()
    cfg = _make_cfg()
    stream = _FakeStream([
        _delta_chunk(None),       # empty content
        _delta_chunk(""),          # empty string
        _delta_chunk("a"),
        SimpleNamespace(choices=[]),  # malformed — no choices
        _delta_chunk("b"),
    ])

    with patch("litellm.acompletion", AsyncMock(return_value=stream)):
        out = [t async for t in router.complete_runtime_stream(
            cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
        )]

    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_complete_runtime_stream_applies_cache_control_for_anthropic():
    """Anthropic models must still get the cache_control breakpoint applied."""
    router = _make_router()
    cfg = _make_cfg(provider_code="anthropic", litellm_name="anthropic/claude-3-5")

    captured: dict = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        return _FakeStream([_delta_chunk("ok")])

    with patch("litellm.acompletion", side_effect=fake_stream):
        async for _ in router.complete_runtime_stream(
            cfg,
            [
                {"role": "system", "content": "stable system"},
                {"role": "user", "content": "Hi"},
            ],
            purpose="generation",
        ):
            pass

    # Verify stream=True passed and system content rewritten as list-of-blocks.
    assert captured["stream"] is True
    sys_content = captured["messages"][0]["content"]
    assert isinstance(sys_content, list)
    assert sys_content[0]["cache_control"] == {"type": "ephemeral"}
    assert sys_content[0]["text"] == "stable system"


@pytest.mark.asyncio
async def test_complete_runtime_stream_upstream_error_surfaces_as_llm_error():
    """Connection / setup failures must raise LLMError so callers map to 5xx."""
    router = _make_router()
    cfg = _make_cfg()

    with patch("litellm.acompletion", AsyncMock(side_effect=ConnectionError("boom"))):
        with pytest.raises(LLMError, match="litellm stream failed"):
            async for _ in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                pass


@pytest.mark.asyncio
async def test_complete_runtime_stream_passes_temperature_override():
    """Caller-provided temperature wins over cfg.params.temperature."""
    router = _make_router()
    cfg = _make_cfg()

    captured: dict = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        return _FakeStream([_delta_chunk("x")])

    with patch("litellm.acompletion", side_effect=fake_stream):
        async for _ in router.complete_runtime_stream(
            cfg,
            [{"role": "user", "content": "Hi"}],
            temperature=0.7,
            purpose="generation",
        ):
            pass

    assert captured["temperature"] == 0.7
