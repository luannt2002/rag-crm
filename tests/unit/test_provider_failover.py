"""LLM provider failover unit tests.

Covers ``DynamicLiteLLMRouter.complete_runtime`` failover wrap:

1. Primary call succeeds → no failover, metric not incremented.
2. Primary raises ``CircuitBreakerOpen`` + fallback configured → fallback
   hop runs, metric=1, response from the fallback returned.
3. Primary fails + ``fallback_model_row_id=None`` → original exception
   re-raises, metric not incremented.
4. Primary + fallback both fail → second exception re-raised (no third
   hop), metric=1 (the failover attempt was made).
5. ``DEFAULT_LLM_FAILOVER_ENABLED=False`` (monkeypatched) → no failover
   even when configured, primary error re-raises.

Strategy: mock the inner ``_complete_runtime_one`` so we drive failover
triggers (``CircuitBreakerOpen`` / ``LLMError``) without spinning up
LiteLLM, retry-with-backoff sleeps, or real circuit breakers. Asserts
hit the prom-client counter ``_value`` directly (low-cardinality) so we
don't need a registry scrape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ragbot.application.dto.model_runtime import (
    Capabilities,
    GenerationParams,
    ModelRuntimeConfig,
    Pricing,
    ProviderRuntime,
)
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.infrastructure.observability.metrics import (
    llm_provider_failover_total,
)
from ragbot.shared.constants import DEFAULT_LLM_MAX_TOKENS
from ragbot.shared.errors import CircuitBreakerOpen, LLMError


# --- helpers ----------------------------------------------------------------

_FIXTURE_CTX_WINDOW = 8 * DEFAULT_LLM_MAX_TOKENS


def _make_provider_rt(code: str) -> ProviderRuntime:
    return ProviderRuntime(
        code=code,
        base_url=f"https://api.{code}.example.com",
        api_key="sk-test-not-real",  # noqa: S106 — fixture, not a real key
    )


def _make_cfg(
    *,
    primary_provider: str = "primary_prov",
    fallback_provider: str | None = "fallback_prov",
    fallback_model: str | None = "fallback-model-name",
) -> ModelRuntimeConfig:
    """Build a ModelRuntimeConfig with optional fallback fields populated."""
    primary_rt = _make_provider_rt(primary_provider)
    fallback_rt = (
        _make_provider_rt(fallback_provider)
        if fallback_provider is not None
        else None
    )
    fallback_wire = (
        f"{fallback_provider}/{fallback_model}"
        if fallback_provider and fallback_model
        else None
    )
    return ModelRuntimeConfig(
        model_row_id=uuid4(),
        binding_id=uuid4(),
        purpose="llm_primary",
        kind="chat",
        provider=primary_rt,
        wire_model_id="primary-model-name",
        litellm_name=f"{primary_provider}/primary-model-name",
        context_window=_FIXTURE_CTX_WINDOW,
        max_output_tokens=DEFAULT_LLM_MAX_TOKENS,
        embedding_dimension=None,
        params=GenerationParams(
            temperature=0.0,
            top_p=1.0,
            max_tokens=DEFAULT_LLM_MAX_TOKENS,
        ),
        pricing=Pricing(
            input_per_1k_usd=Decimal("0.001"),
            output_per_1k_usd=Decimal("0.002"),
        ),
        capabilities=Capabilities(),
        quality_tier="cheap",
        version_hash="testhash",
        loaded_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
        fallback_model_row_id=uuid4() if fallback_provider else None,
        fallback_wire_model_id=fallback_wire,
        fallback_provider=fallback_rt,
    )


def _make_router() -> DynamicLiteLLMRouter:
    """DynamicLiteLLMRouter with a stub repo (no DB / no Redis)."""
    repo = AsyncMock()
    return DynamicLiteLLMRouter(ai_config_repo=repo, redis_client=None)


def _counter_value(*, from_provider: str, to_provider: str, purpose: str, reason: str) -> float:
    """Return the current Prometheus counter value for the given labels."""
    return llm_provider_failover_total.labels(
        from_provider=from_provider,
        to_provider=to_provider,
        purpose=purpose,
        reason=reason,
    )._value.get()


# --- tests ------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_primary_ok_no_failover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful primary call must not invoke the fallback or bump the metric."""
    router = _make_router()
    cfg = _make_cfg()

    expected = {
        "text": "ok",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    }
    inner = AsyncMock(return_value=expected)
    monkeypatch.setattr(router, "_complete_runtime_one", inner)

    before = _counter_value(
        from_provider="primary_prov",
        to_provider="fallback_prov",
        purpose="generation",
        reason="LLMError",
    )

    out = await router.complete_runtime(cfg, [{"role": "user", "content": "Hi"}], purpose="generation")

    assert out == expected
    assert inner.await_count == 1
    after = _counter_value(
        from_provider="primary_prov",
        to_provider="fallback_prov",
        purpose="generation",
        reason="LLMError",
    )
    assert after == before


@pytest.mark.asyncio()
async def test_circuit_breaker_open_triggers_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CircuitBreakerOpen`` on primary must trigger the fallback hop and bump the metric."""
    router = _make_router()
    cfg = _make_cfg(primary_provider="cb_open_prim", fallback_provider="cb_open_fb")

    fallback_response = {
        "text": "served-by-fallback",
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    }

    call_log: list[str] = []

    async def _fake_inner(cfg_arg, _messages, **kwargs):
        # Use the resolved provider code to detect which hop is running.
        provider_code = cfg_arg.provider.code
        call_log.append(provider_code)
        if provider_code == "cb_open_prim":
            raise CircuitBreakerOpen("primary CB OPEN")
        # Fallback hop must run with apply_anthropic_cache=False.
        assert kwargs.get("apply_anthropic_cache") is False
        return fallback_response

    monkeypatch.setattr(router, "_complete_runtime_one", _fake_inner)

    before = _counter_value(
        from_provider="cb_open_prim",
        to_provider="cb_open_fb",
        purpose="generation",
        reason="CircuitBreakerOpen",
    )
    out = await router.complete_runtime(cfg, [{"role": "user", "content": "Hi"}], purpose="generation")
    after = _counter_value(
        from_provider="cb_open_prim",
        to_provider="cb_open_fb",
        purpose="generation",
        reason="CircuitBreakerOpen",
    )

    assert out == fallback_response
    # Hop sequence: primary first, then fallback.
    assert call_log == ["cb_open_prim", "cb_open_fb"]
    assert after - before == pytest.approx(1.0)


@pytest.mark.asyncio()
async def test_no_fallback_configured_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the binding has no fallback configured the primary error must propagate."""
    router = _make_router()
    cfg = _make_cfg(
        primary_provider="solo_prim",
        fallback_provider=None,
        fallback_model=None,
    )
    assert cfg.fallback_model_row_id is None

    inner = AsyncMock(side_effect=LLMError("primary failed"))
    monkeypatch.setattr(router, "_complete_runtime_one", inner)

    before = _counter_value(
        from_provider="solo_prim",
        to_provider="unknown",
        purpose="generation",
        reason="LLMError",
    )

    with pytest.raises(LLMError, match="primary failed"):
        await router.complete_runtime(cfg, [{"role": "user", "content": "Hi"}], purpose="generation")

    # Only the primary was called.
    assert inner.await_count == 1
    after = _counter_value(
        from_provider="solo_prim",
        to_provider="unknown",
        purpose="generation",
        reason="LLMError",
    )
    assert after == before


@pytest.mark.asyncio()
async def test_both_primary_and_fallback_fail_reraises_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both primary and fallback fail → second exception re-raised, no third hop, metric=1."""
    router = _make_router()
    cfg = _make_cfg(primary_provider="dbl_prim", fallback_provider="dbl_fb")

    call_log: list[str] = []

    async def _fake_inner(cfg_arg, _messages, **_kwargs):
        provider_code = cfg_arg.provider.code
        call_log.append(provider_code)
        if provider_code == "dbl_prim":
            raise LLMError("primary boom")
        raise LLMError("fallback boom")

    monkeypatch.setattr(router, "_complete_runtime_one", _fake_inner)

    before = _counter_value(
        from_provider="dbl_prim",
        to_provider="dbl_fb",
        purpose="generation",
        reason="LLMError",
    )

    with pytest.raises(LLMError, match="fallback boom"):
        await router.complete_runtime(cfg, [{"role": "user", "content": "Hi"}], purpose="generation")

    # Exactly two hops — no third try.
    assert call_log == ["dbl_prim", "dbl_fb"]
    after = _counter_value(
        from_provider="dbl_prim",
        to_provider="dbl_fb",
        purpose="generation",
        reason="LLMError",
    )
    # The failover attempt was logged before the fallback ran.
    assert after - before == pytest.approx(1.0)


@pytest.mark.asyncio()
async def test_failover_disabled_by_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global kill switch ``DEFAULT_LLM_FAILOVER_ENABLED=False`` disables the hop."""
    router = _make_router()
    cfg = _make_cfg(primary_provider="killed_prim", fallback_provider="killed_fb")

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.DEFAULT_LLM_FAILOVER_ENABLED",
        False,
    )

    inner = AsyncMock(side_effect=LLMError("primary still fails"))
    monkeypatch.setattr(router, "_complete_runtime_one", inner)

    before = _counter_value(
        from_provider="killed_prim",
        to_provider="killed_fb",
        purpose="generation",
        reason="LLMError",
    )

    with pytest.raises(LLMError, match="primary still fails"):
        await router.complete_runtime(cfg, [{"role": "user", "content": "Hi"}], purpose="generation")

    # Only one hop ran — fallback skipped because the kill switch is OFF.
    assert inner.await_count == 1
    after = _counter_value(
        from_provider="killed_prim",
        to_provider="killed_fb",
        purpose="generation",
        reason="LLMError",
    )
    assert after == before


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
