""": Anthropic prompt cache_control helper + provider auto-detect.

Verifies _apply_anthropic_cache_control wraps the system prompt for Anthropic
models only, leaving OpenAI / unknown providers untouched. Also verifies the
prompt_cache_hits_total Counter increments when complete_runtime sees
cached_tokens > 0 in usage.prompt_tokens_details.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.infrastructure.llm.dynamic_litellm_router import (
    DynamicLiteLLMRouter,
    _apply_anthropic_cache_control,
    _is_anthropic_model,
)


# ---------- _is_anthropic_model -------------------------------------------------

def test_is_anthropic_model_matches_anthropic_prefix():
    assert _is_anthropic_model("anthropic/claude-3-5-sonnet-20240620", None) is True


def test_is_anthropic_model_matches_provider_code():
    assert _is_anthropic_model("custom/model-x", "anthropic") is True
    assert _is_anthropic_model("custom/claude-haiku", None) is True


def test_is_anthropic_model_rejects_openai():
    assert _is_anthropic_model("openai/gpt-4.1-mini", "openai") is False


def test_is_anthropic_model_rejects_none():
    assert _is_anthropic_model(None, None) is False


# ---------- _apply_anthropic_cache_control --------------------------------------

def test_apply_cache_control_noop_for_openai():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hi"},
    ]
    out = _apply_anthropic_cache_control(
        msgs, litellm_name="openai/gpt-4.1-mini", provider_code="openai"
    )
    # Returned unchanged — same content type (str), no cache_control breakpoint.
    assert out is msgs or out == msgs
    assert out[0]["content"] == "You are helpful."


def test_apply_cache_control_wraps_anthropic_system():
    msgs = [
        {"role": "system", "content": "You are a docs-only assistant."},
        {"role": "user", "content": "What is X?"},
    ]
    out = _apply_anthropic_cache_control(
        msgs, litellm_name="anthropic/claude-3-5-sonnet", provider_code="anthropic"
    )
    # System content rewritten to list-of-blocks with cache_control breakpoint.
    assert out[0]["role"] == "system"
    sys_content = out[0]["content"]
    assert isinstance(sys_content, list)
    assert sys_content[0]["type"] == "text"
    assert sys_content[0]["text"] == "You are a docs-only assistant."
    assert sys_content[0]["cache_control"] == {"type": "ephemeral"}
    # Other messages untouched.
    assert out[1] == {"role": "user", "content": "What is X?"}
    # Caller's original list not mutated.
    assert isinstance(msgs[0]["content"], str)


def test_apply_cache_control_skips_non_string_system():
    """If caller already provided multi-block system content, leave it alone
    so the caller controls all 4 Anthropic breakpoints."""
    msgs = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "block-1"}],
        },
        {"role": "user", "content": "Hi"},
    ]
    out = _apply_anthropic_cache_control(
        msgs, litellm_name="anthropic/claude-3-5", provider_code=None
    )
    assert out[0]["content"] == [{"type": "text", "text": "block-1"}]


def test_apply_cache_control_skips_when_no_system_first():
    msgs = [{"role": "user", "content": "Hi"}]
    out = _apply_anthropic_cache_control(
        msgs, litellm_name="anthropic/claude-3-5", provider_code=None
    )
    assert out == msgs


def test_apply_cache_control_empty_messages_safe():
    out = _apply_anthropic_cache_control(
        [], litellm_name="anthropic/claude", provider_code="anthropic"
    )
    assert out == []


# ---------- complete_runtime — metric increment + purpose label -----------------

def _make_router() -> DynamicLiteLLMRouter:
    repo = AsyncMock()
    return DynamicLiteLLMRouter(ai_config_repo=repo)


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


def _make_response_with_cache(prompt: int, cached: int, completion: int = 5):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="hi"),
            finish_reason="stop",
        )],
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    )


@pytest.mark.asyncio
async def test_complete_runtime_increments_cache_hit_metric_for_openai():
    """When OpenAI auto-cache reports cached_tokens > 0, the Counter must
    increment with provider='openai' + the propagated purpose label."""
    from ragbot.infrastructure.observability.metrics import (
        prompt_cache_hits_total,
        prompt_cache_tokens_saved_total,
    )

    router = _make_router()
    cfg = _make_cfg(provider_code="openai", litellm_name="openai/gpt-4.1-mini")

    before_hits = prompt_cache_hits_total.labels(
        provider="openai", purpose="generation",
    )._value.get()
    before_saved = prompt_cache_tokens_saved_total.labels(
        provider="openai", purpose="generation",
    )._value.get()

    resp = _make_response_with_cache(prompt=2000, cached=1500)
    with patch("litellm.acompletion", AsyncMock(return_value=resp)):
        result = await router.complete_runtime(
            cfg,
            [
                {"role": "system", "content": "long system prompt..."},
                {"role": "user", "content": "Hi"},
            ],
            purpose="generation",
        )

    assert result["cached_tokens"] == 1500
    after_hits = prompt_cache_hits_total.labels(
        provider="openai", purpose="generation",
    )._value.get()
    after_saved = prompt_cache_tokens_saved_total.labels(
        provider="openai", purpose="generation",
    )._value.get()
    assert after_hits == before_hits + 1
    assert after_saved == before_saved + 1500


@pytest.mark.asyncio
async def test_complete_runtime_no_metric_when_zero_cached():
    """No cached_tokens → no metric increment. (Counter only fires on hits.)"""
    from ragbot.infrastructure.observability.metrics import prompt_cache_hits_total

    router = _make_router()
    cfg = _make_cfg(provider_code="openai")

    before = prompt_cache_hits_total.labels(
        provider="openai", purpose="grading",
    )._value.get()

    resp = _make_response_with_cache(prompt=500, cached=0)
    with patch("litellm.acompletion", AsyncMock(return_value=resp)):
        await router.complete_runtime(
            cfg,
            [{"role": "user", "content": "Hi"}],
            purpose="grading",
        )

    after = prompt_cache_hits_total.labels(
        provider="openai", purpose="grading",
    )._value.get()
    assert after == before  # untouched


@pytest.mark.asyncio
async def test_complete_runtime_applies_cache_control_for_anthropic():
    """Anthropic model → litellm.acompletion called with system content
    rewritten as cache_control list-of-blocks."""
    router = _make_router()
    cfg = _make_cfg(provider_code="anthropic", litellm_name="anthropic/claude-3-5-sonnet")

    captured: dict = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return _make_response_with_cache(prompt=10, cached=0)

    with patch("litellm.acompletion", side_effect=fake_complete):
        await router.complete_runtime(
            cfg,
            [
                {"role": "system", "content": "stable system text"},
                {"role": "user", "content": "Hi"},
            ],
            purpose="generation",
        )

    sent_msgs = captured["messages"]
    sys_content = sent_msgs[0]["content"]
    assert isinstance(sys_content, list)
    assert sys_content[0]["cache_control"] == {"type": "ephemeral"}
    assert sys_content[0]["text"] == "stable system text"
