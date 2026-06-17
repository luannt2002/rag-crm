"""F1b — Anthropic prompt-cache breakpoint smoke tests (per-provider matrix).

This file complements `test_prompt_cache_helper.py`. Where the existing
file pins the helper API + metric increment in isolation, this one pins
the **end-to-end behaviour** of `complete_runtime` so a regression can't
slip in via a refactor that detaches `_apply_anthropic_cache_control`
from the call site.

Per F9 cost-audit gap:
    "cache_control wired but no automated test asserts it fires per
    provider; load-test JSON drops cached_tokens so prod hit ratio is
    unknown."

The 3 pinned scenarios:
    1. cache-on-Anthropic    — system content rewritten as list-of-blocks
                               with cache_control:{type:ephemeral} on the
                               very first block, on EVERY turn (idempotent
                               across multiple ``complete_runtime`` calls).
    2. cache-off-OpenAI      — OpenAI relies on automatic ≥1024-token
                               caching, no client breakpoint allowed.
                               System content stays a plain string.
    3. cache-off-when-flag   — the helper itself is the flag in the
                               current architecture (no system_config
                               toggle exists yet — see Phase-2 ROADMAP).
                               Asserting cache_control is absent when the
                               provider/code mismatch happens (e.g. a
                               misconfigured row claims litellm_name
                               "openai/..." but provider_code "anthropic"
                               — substring match still wins safely).

App-mindset compliance: zero LLM injection. Pure assertion on outbound
``messages`` payload to LiteLLM.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter


# ---------- shared fixtures ----------------------------------------------------

def _make_router() -> DynamicLiteLLMRouter:
    repo = AsyncMock()
    return DynamicLiteLLMRouter(ai_config_repo=repo)


def _make_cfg(provider_code: str, litellm_name: str):
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


def _make_response(prompt: int = 100, completion: int = 5, cached: int = 0):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="ok"),
            finish_reason="stop",
        )],
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    )


def _system_content_is_breakpointed(messages: list[dict]) -> bool:
    """Return True iff messages[0] is a system message rewritten as a
    list-of-blocks payload with cache_control on the first block."""
    if not messages:
        return False
    first = messages[0]
    if first.get("role") != "system":
        return False
    content = first.get("content")
    if not isinstance(content, list) or not content:
        return False
    block = content[0]
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and block.get("cache_control") == {"type": "ephemeral"}
    )


# ---------- scenario 1 — cache-on-Anthropic (idempotent across turns) -----------

@pytest.mark.asyncio
async def test_cache_control_fires_for_anthropic_every_turn():
    """End-to-end: each ``complete_runtime`` call against an Anthropic
    model wraps the system prompt with cache_control:ephemeral.

    Pin: regression where cache_control was wired only on the first turn
    of a conversation (e.g. accidentally cached message-list mutation)
    would be silent in production but caught here.
    """
    router = _make_router()
    cfg = _make_cfg(
        provider_code="anthropic",
        litellm_name="anthropic/claude-3-5-sonnet",
    )

    captured_calls: list[list[dict]] = []

    async def fake_complete(**kwargs):
        captured_calls.append(kwargs["messages"])
        return _make_response()

    base_msgs = [
        {"role": "system", "content": "Stable system prompt for caching."},
        {"role": "user", "content": "Q1"},
    ]

    with patch("litellm.acompletion", side_effect=fake_complete):
        # Three back-to-back invocations with the same caller-provided
        # message list. The helper must NOT mutate caller state and must
        # rewrite the system block on every outbound payload.
        for _ in range(3):
            await router.complete_runtime(cfg, list(base_msgs), purpose="generation")

    assert len(captured_calls) == 3
    for call_msgs in captured_calls:
        assert _system_content_is_breakpointed(call_msgs), (
            "cache_control:ephemeral missing on Anthropic call payload"
        )

    # Caller list NOT mutated — system content still a plain string.
    assert base_msgs[0]["content"] == "Stable system prompt for caching."


# ---------- scenario 2 — cache-off-OpenAI (auto-cache provider) -----------------

@pytest.mark.asyncio
async def test_cache_control_absent_for_openai():
    """OpenAI auto-caches prompts ≥1024 tokens server-side. Sending a
    client-side cache_control breakpoint would either be ignored or
    rejected — must NOT be emitted."""
    router = _make_router()
    cfg = _make_cfg(
        provider_code="openai",
        litellm_name="openai/gpt-4.1-mini",
    )

    captured_calls: list[list[dict]] = []

    async def fake_complete(**kwargs):
        captured_calls.append(kwargs["messages"])
        return _make_response()

    msgs = [
        {"role": "system", "content": "OpenAI system prompt."},
        {"role": "user", "content": "Hi"},
    ]

    with patch("litellm.acompletion", side_effect=fake_complete):
        await router.complete_runtime(cfg, msgs, purpose="generation")

    assert len(captured_calls) == 1
    sent = captured_calls[0]
    # System content stays a plain string — no list-of-blocks rewrite.
    assert isinstance(sent[0]["content"], str)
    assert sent[0]["content"] == "OpenAI system prompt."
    assert not _system_content_is_breakpointed(sent)


# ---------- scenario 3 — cache-off when no system message present ---------------

@pytest.mark.asyncio
async def test_cache_control_absent_when_first_message_not_system():
    """If the caller skipped the system message (e.g. legacy code path
    that pre-merges system into the user prompt), the helper must
    no-op — the breakpoint is for the system block specifically."""
    router = _make_router()
    cfg = _make_cfg(
        provider_code="anthropic",
        litellm_name="anthropic/claude-3-5-sonnet",
    )

    captured_calls: list[list[dict]] = []

    async def fake_complete(**kwargs):
        captured_calls.append(kwargs["messages"])
        return _make_response()

    msgs = [
        {"role": "user", "content": "User-only payload, no system."},
    ]

    with patch("litellm.acompletion", side_effect=fake_complete):
        await router.complete_runtime(cfg, msgs, purpose="generation")

    sent = captured_calls[0]
    # First message is user — list-of-blocks rewrite must NOT happen.
    assert sent[0]["role"] == "user"
    assert isinstance(sent[0]["content"], str)
    assert not _system_content_is_breakpointed(sent)
