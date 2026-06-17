"""Integration: OpenAI primary 5xx → Anthropic claude-haiku-4-5 fallback.

Verifies the SPOF closure shipped by alembic 0070
(``20260508_0070_seed_anthropic_fallback.py``):

1. ``ModelResolverService.resolve_runtime`` returns a ``ModelRuntimeConfig``
   where ``fallback_wire_model_id == "anthropic/claude-haiku-4-5-20251001"``
   when the binding's ``record_fallback_model_id`` points at the seeded
   Haiku row. This is the data-layer contract — without it the failover
   wrap in ``DynamicLiteLLMRouter`` has nothing to dispatch to.

2. The config flowing into ``DynamicLiteLLMRouter.complete_runtime`` makes
   the failover wrap engage when the primary raises ``CircuitBreakerOpen``
   or a retryable ``LLMError`` — assert via the call-log + metric
   ``llm_provider_failover_total{from_provider=openai,to_provider=anthropic}``.

3. Per-binding opt-out is preserved: a binding with
   ``record_fallback_model_id IS NULL`` (operator opted out post-migration)
   surfaces an empty fallback on the runtime config and the router
   re-raises the primary error.

NO live network calls — both Anthropic and OpenAI are fully mocked. The
``AIConfigRepositoryPort`` is stubbed with rows that mirror the post-0070
DB state exactly (Anthropic provider row + claude-haiku-4-5 model row +
generation binding wired with ``record_fallback_model_id``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.dto.model_runtime import ModelRuntimeConfig
from ragbot.application.ports.ai_config_port import (
    BindingRow,
    ModelRow,
    ProviderRow,
)
from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.infrastructure.observability.metrics import (
    llm_provider_failover_total,
)
from ragbot.shared.clock import FrozenClock
from ragbot.shared.errors import CircuitBreakerOpen, LLMError


# --- Fixtures mirroring post-0070 DB state ----------------------------------

# Stable UUIDs from the migration constants — keeping them in sync with the
# alembic seed proves the test would actually exercise the seeded rows in
# a real environment rather than a parallel-but-different fixture.
ANTHROPIC_PROVIDER_ID = UUID("a5e3c2f1-7b9d-4e8a-9c1d-3f5b8a2e4d6c")
CLAUDE_HAIKU_MODEL_ID = UUID("c8f7d1a2-9e4b-4f3c-8d6a-1e2b5c7d9a3f")

# Synthetic OpenAI provider+model UUIDs (test-local — the real DB row uses
# different UUIDs but the resolver only cares about referential integrity
# inside the mocked repo, not the exact values).
OPENAI_PROVIDER_ID = UUID("11111111-1111-4111-9111-111111111111")
GPT_MODEL_ID = UUID("22222222-2222-4222-9222-222222222222")
TEST_TENANT_ID = UUID("33333333-3333-4333-9333-333333333333")
TEST_BOT_ID = UUID("44444444-4444-4444-9444-444444444444")
BINDING_ID = UUID("55555555-5555-4555-9555-555555555555")


def _openai_provider_row() -> ProviderRow:
    return ProviderRow(
        id=OPENAI_PROVIDER_ID,
        name="openai",
        code="openai",
        type="llm",
        base_url="https://api.openai.com/v1",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
        metadata={},
    )


def _anthropic_provider_row() -> ProviderRow:
    return ProviderRow(
        id=ANTHROPIC_PROVIDER_ID,
        name="anthropic",
        code="anthropic",
        type="llm",
        base_url="https://api.anthropic.com",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
        metadata={},
    )


def _gpt_model_row() -> ModelRow:
    return ModelRow(
        id=GPT_MODEL_ID,
        provider_id=OPENAI_PROVIDER_ID,
        name="gpt-4.1-mini",
        kind="llm",
        context_window=128000,
        max_output_tokens=4096,
        input_price_per_1k_usd=Decimal("0.000400"),
        output_price_per_1k_usd=Decimal("0.001600"),
        supports_streaming=True,
        supports_tools=False,
        supports_vision=False,
        supports_json_mode=True,
        languages=("vi", "en"),
        enabled=True,
        metadata={},
    )


def _claude_haiku_model_row() -> ModelRow:
    """Mirrors the row the migration INSERTs."""
    return ModelRow(
        id=CLAUDE_HAIKU_MODEL_ID,
        provider_id=ANTHROPIC_PROVIDER_ID,
        name="claude-haiku-4-5-20251001",
        kind="llm",
        context_window=200000,
        max_output_tokens=8192,
        input_price_per_1k_usd=Decimal("0.001000"),
        output_price_per_1k_usd=Decimal("0.005000"),
        supports_streaming=True,
        supports_tools=True,
        supports_vision=False,
        supports_json_mode=True,
        languages=("vi", "en"),
        enabled=True,
        metadata={},
    )


def _generation_binding(*, with_fallback: bool) -> BindingRow:
    """Generation binding — with or without fallback wired (per-bot opt-out)."""
    return BindingRow(
        id=BINDING_ID,
        record_tenant_id=TEST_TENANT_ID,
        record_bot_id=TEST_BOT_ID,
        purpose="generation",
        model_id=GPT_MODEL_ID,
        rank=0,
        variant=None,
        weight=100,
        temperature=0.2,
        max_tokens=1024,
        top_p=0.9,
        extra_params={},
        active=True,
        version=1,
        record_fallback_model_id=CLAUDE_HAIKU_MODEL_ID if with_fallback else None,
    )


def _make_repo(*, with_fallback: bool) -> AsyncMock:
    """Stub AIConfigRepositoryPort returning post-0070 rows."""
    repo = AsyncMock()

    binding = _generation_binding(with_fallback=with_fallback)
    repo.list_bindings.return_value = [binding]

    async def _get_provider(provider_id: UUID) -> ProviderRow | None:
        if provider_id == OPENAI_PROVIDER_ID:
            return _openai_provider_row()
        if provider_id == ANTHROPIC_PROVIDER_ID:
            return _anthropic_provider_row()
        return None

    async def _get_model(model_id: UUID) -> ModelRow | None:
        if model_id == GPT_MODEL_ID:
            return _gpt_model_row()
        if model_id == CLAUDE_HAIKU_MODEL_ID:
            return _claude_haiku_model_row()
        return None

    repo.get_provider.side_effect = _get_provider
    repo.get_model.side_effect = _get_model
    return repo


def _make_cache() -> AsyncMock:
    """Cache stub: every get is a miss, set is a no-op."""
    cache = AsyncMock()
    cache.get.return_value = None
    cache.set.return_value = None
    cache.delete.return_value = None
    return cache


def _make_clock() -> FrozenClock:
    """Production-shaped Clock (now + monotonic) with a fixed wall-time."""
    return FrozenClock(initial=datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc))


# --- Test 1: resolver returns config wired with Anthropic fallback ---------


@pytest.mark.asyncio()
async def test_resolver_wires_anthropic_fallback_when_binding_set() -> None:
    """post-0070: resolve_runtime returns config with anthropic/haiku fallback."""
    repo = _make_repo(with_fallback=True)
    resolver = ModelResolverService(repo=repo, cache=_make_cache(), clock=_make_clock())

    cfg: ModelRuntimeConfig = await resolver.resolve_runtime(
        record_tenant_id=TEST_TENANT_ID,
        record_bot_id=TEST_BOT_ID,
        purpose="generation",
    )

    # Primary is OpenAI gpt-4.1-mini.
    assert cfg.provider.code == "openai"
    assert cfg.wire_model_id == "gpt-4.1-mini"
    assert cfg.litellm_name == "openai/gpt-4.1-mini"

    # Fallback wired to Anthropic claude-haiku-4-5 (the SPOF-closure assertion).
    assert cfg.fallback_model_row_id == CLAUDE_HAIKU_MODEL_ID
    assert cfg.fallback_wire_model_id == "anthropic/claude-haiku-4-5-20251001"
    assert cfg.fallback_provider is not None
    assert cfg.fallback_provider.code == "anthropic"
    assert cfg.fallback_provider.base_url == "https://api.anthropic.com"


# --- Test 2: per-bot opt-out (binding with NULL fallback) leaves config bare


@pytest.mark.asyncio()
async def test_resolver_omits_fallback_when_binding_null() -> None:
    """Per-bot opt-out: ``record_fallback_model_id IS NULL`` → no fallback in cfg."""
    repo = _make_repo(with_fallback=False)
    resolver = ModelResolverService(repo=repo, cache=_make_cache(), clock=_make_clock())

    cfg = await resolver.resolve_runtime(
        record_tenant_id=TEST_TENANT_ID,
        record_bot_id=TEST_BOT_ID,
        purpose="generation",
    )

    assert cfg.fallback_model_row_id is None
    assert cfg.fallback_wire_model_id is None
    assert cfg.fallback_provider is None


# --- Test 3: router engages fallback when primary CB opens ------------------


def _counter_value(*, from_provider: str, to_provider: str, purpose: str, reason: str) -> float:
    return llm_provider_failover_total.labels(
        from_provider=from_provider,
        to_provider=to_provider,
        purpose=purpose,
        reason=reason,
    )._value.get()


@pytest.mark.asyncio()
async def test_router_engages_anthropic_fallback_on_primary_circuit_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end (mocked): resolver → router → fallback hop on CB open.

    Builds the runtime config the same way the resolver would (post-0070),
    feeds it into the router, monkeypatches the inner single-hop call to
    raise ``CircuitBreakerOpen`` on the OpenAI hop, and asserts (a) the
    response comes from the Anthropic mock, (b) the metric increments,
    (c) the fallback hop runs with ``apply_anthropic_cache=False`` (set by
    the router's failover wrap to avoid cache-namespace corruption).
    """
    repo = _make_repo(with_fallback=True)
    resolver = ModelResolverService(repo=repo, cache=_make_cache(), clock=_make_clock())
    cfg = await resolver.resolve_runtime(
        record_tenant_id=TEST_TENANT_ID,
        record_bot_id=TEST_BOT_ID,
        purpose="generation",
    )

    router = DynamicLiteLLMRouter(ai_config_repo=AsyncMock(), redis_client=None)

    fallback_response = {
        "text": "served-by-anthropic-haiku-fallback",
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    }

    call_log: list[tuple[str, bool]] = []

    async def _fake_inner(cfg_arg, _messages, **kwargs):
        provider_code = cfg_arg.provider.code
        # Track whether this hop received apply_anthropic_cache=False.
        call_log.append((provider_code, kwargs.get("apply_anthropic_cache", True)))
        if provider_code == "openai":
            raise CircuitBreakerOpen("primary OpenAI CB OPEN")
        # Fallback hop must be Anthropic and must NOT carry the cache flag.
        return fallback_response

    monkeypatch.setattr(router, "_complete_runtime_one", _fake_inner)

    before = _counter_value(
        from_provider="openai",
        to_provider="anthropic",
        purpose="generation",
        reason="CircuitBreakerOpen",
    )
    out = await router.complete_runtime(
        cfg,
        [{"role": "user", "content": "Hi"}],
        purpose="generation",
    )
    after = _counter_value(
        from_provider="openai",
        to_provider="anthropic",
        purpose="generation",
        reason="CircuitBreakerOpen",
    )

    # Response routed through Anthropic.
    assert out == fallback_response
    # Hop sequence: OpenAI primary fails → Anthropic fallback succeeds.
    assert [code for code, _ in call_log] == ["openai", "anthropic"]
    # Fallback hop ran with apply_anthropic_cache=False (router's contract).
    assert call_log[1][1] is False
    # Metric label set for the actual provider transition.
    assert after - before == pytest.approx(1.0)


# --- Test 4: router re-raises when binding has no fallback ------------------


@pytest.mark.asyncio()
async def test_router_reraises_when_binding_opts_out_of_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No fallback configured → primary error propagates, no fallback hop runs."""
    repo = _make_repo(with_fallback=False)
    resolver = ModelResolverService(repo=repo, cache=_make_cache(), clock=_make_clock())
    cfg = await resolver.resolve_runtime(
        record_tenant_id=TEST_TENANT_ID,
        record_bot_id=TEST_BOT_ID,
        purpose="generation",
    )
    assert cfg.fallback_model_row_id is None  # post-condition from resolver

    router = DynamicLiteLLMRouter(ai_config_repo=AsyncMock(), redis_client=None)

    inner = AsyncMock(side_effect=LLMError("primary OpenAI failed"))
    monkeypatch.setattr(router, "_complete_runtime_one", inner)

    with pytest.raises(LLMError, match="primary OpenAI failed"):
        await router.complete_runtime(
            cfg,
            [{"role": "user", "content": "Hi"}],
            purpose="generation",
        )

    # Exactly one hop — no fallback dispatch attempted.
    assert inner.await_count == 1
