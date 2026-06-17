"""Per-purpose LLM binding (cost-aware route) unit tests.

Covers:

1. ``resolve_purpose_for_intent`` mapping for each canonical intent.
2. ``ModelResolverService.resolve_runtime`` fallback chain when the
   cheap-purpose binding row is absent (must transparently re-query with
   ``purpose=llm_primary`` and return the primary binding).

The tests stay strategy/DI-clean by mocking the ``AIConfigRepositoryPort``
only — no DB / network / LiteLLM contact — and by referencing
``DEFAULT_LLM_PURPOSE_*`` constants instead of hard-coding the string
literals.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.ports.ai_config_port import (
    BindingRow,
    ModelRow,
    ProviderRow,
)
from ragbot.application.services.model_resolver import (
    ModelResolverService,
    resolve_purpose_for_intent,
)
from ragbot.shared.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_PURPOSE_CHITCHAT,
    DEFAULT_LLM_PURPOSE_FACTOID,
    DEFAULT_LLM_PURPOSE_OOS,
    DEFAULT_LLM_PURPOSE_PRIMARY,
)

# Test-fixture-only context window — large enough that
# ``max_output_tokens=DEFAULT_LLM_MAX_TOKENS`` always fits.
_FIXTURE_CONTEXT_WINDOW = 8 * DEFAULT_LLM_MAX_TOKENS


# --- helpers ----------------------------------------------------------------

def _row_factory(*, purpose: str) -> tuple[ProviderRow, ModelRow, BindingRow]:
    """Build coherent (provider, model, binding) DTOs for a given purpose."""
    provider_id = uuid4()
    model_id = uuid4()
    binding_id = uuid4()
    tenant_id = uuid4()
    bot_id = uuid4()

    provider = ProviderRow(
        id=provider_id,
        name="Test Provider",
        code="testprov",
        type="llm",
        base_url="https://api.example.com",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
    )
    model = ModelRow(
        id=model_id,
        provider_id=provider_id,
        name=f"{purpose}-model",
        kind="chat",
        context_window=_FIXTURE_CONTEXT_WINDOW,
        max_output_tokens=DEFAULT_LLM_MAX_TOKENS,
        input_price_per_1k_usd=Decimal("0.001"),
        output_price_per_1k_usd=Decimal("0.002"),
        supports_streaming=True,
        supports_tools=False,
        supports_vision=False,
        supports_json_mode=False,
        languages=("en",),
        enabled=True,
    )
    binding = BindingRow(
        id=binding_id,
        record_tenant_id=tenant_id,
        record_bot_id=bot_id,
        purpose=purpose,
        model_id=model_id,
        rank=0,
        variant=None,
        weight=100,
        temperature=0.0,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        top_p=1.0,
        extra_params={},
        active=True,
        version=1,
    )
    return provider, model, binding


def _build_service_with_repo(repo: MagicMock) -> ModelResolverService:
    """Construct a ``ModelResolverService`` with mocked dependencies."""
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()

    clock = MagicMock()
    # Monotonic must monotonically increase so cache-hit checks are stable.
    clock.monotonic.side_effect = lambda: 0.0
    from datetime import datetime, timezone
    clock.now.return_value = datetime(2026, 5, 4, tzinfo=timezone.utc)

    return ModelResolverService(repo=repo, cache=cache, clock=clock)


# --- intent → purpose mapping (5 covered intents + 1 fallback) -------------

def test_factoid_intent_uses_factoid_purpose() -> None:
    """``factoid`` intent must route to the factoid binding purpose."""
    assert resolve_purpose_for_intent("factoid") == DEFAULT_LLM_PURPOSE_FACTOID


def test_multi_hop_uses_primary() -> None:
    """``multi_hop`` is NOT a cheap-route intent → must stay on primary."""
    assert resolve_purpose_for_intent("multi_hop") == DEFAULT_LLM_PURPOSE_PRIMARY


def test_chitchat_uses_chitchat_purpose() -> None:
    """``chitchat`` + the ``greeting`` synonym route to the chitchat binding."""
    assert resolve_purpose_for_intent("chitchat") == DEFAULT_LLM_PURPOSE_CHITCHAT
    assert resolve_purpose_for_intent("greeting") == DEFAULT_LLM_PURPOSE_CHITCHAT


def test_out_of_scope_uses_oos_purpose() -> None:
    """``out_of_scope`` and ``vu_vo`` (VN slang) both route to the OOS binding."""
    assert resolve_purpose_for_intent("out_of_scope") == DEFAULT_LLM_PURPOSE_OOS
    assert resolve_purpose_for_intent("vu_vo") == DEFAULT_LLM_PURPOSE_OOS


def test_unknown_intent_falls_back_to_primary() -> None:
    """Unknown / None / empty intent must default to the primary binding."""
    assert resolve_purpose_for_intent(None) == DEFAULT_LLM_PURPOSE_PRIMARY
    assert resolve_purpose_for_intent("") == DEFAULT_LLM_PURPOSE_PRIMARY
    assert resolve_purpose_for_intent("nonexistent_xyz") == DEFAULT_LLM_PURPOSE_PRIMARY


# --- resolver fallback when cheap binding absent ---------------------------

@pytest.mark.asyncio()
async def test_resolver_falls_back_when_cheap_binding_absent() -> None:
    """When ``list_bindings(purpose='llm_factoid')`` returns empty,
    ``resolve_runtime`` must transparently re-query with
    ``purpose='llm_primary'`` and return the primary binding's runtime
    config — keeping per-bot opt-out implicit.
    """
    primary_provider, primary_model, primary_binding = _row_factory(
        purpose=DEFAULT_LLM_PURPOSE_PRIMARY,
    )

    repo = MagicMock()

    list_calls: list[str] = []

    async def _list_bindings(*, record_tenant_id, record_bot_id, purpose=None, active_only=True):  # noqa: ARG001
        list_calls.append(purpose)
        if purpose == DEFAULT_LLM_PURPOSE_FACTOID:
            return []  # cheap binding absent
        if purpose == DEFAULT_LLM_PURPOSE_PRIMARY:
            return [primary_binding]
        return []

    repo.list_bindings = AsyncMock(side_effect=_list_bindings)
    repo.get_model = AsyncMock(return_value=primary_model)
    repo.get_provider = AsyncMock(return_value=primary_provider)

    svc = _build_service_with_repo(repo)

    cfg = await svc.resolve_runtime(
        record_tenant_id=primary_binding.record_tenant_id,
        record_bot_id=primary_binding.record_bot_id,
        purpose=DEFAULT_LLM_PURPOSE_FACTOID,
    )

    # Ordering matters — first attempt with the cheap purpose, then fallback.
    assert list_calls == [DEFAULT_LLM_PURPOSE_FACTOID, DEFAULT_LLM_PURPOSE_PRIMARY]

    # Returned runtime config must be backed by the primary binding row.
    assert cfg.binding_id == primary_binding.id
    assert cfg.model_row_id == primary_model.id
    # The lookup key (purpose label) flows through unchanged so observability
    # can see ``factoid`` was the requested purpose even though primary served.
    assert cfg.purpose == DEFAULT_LLM_PURPOSE_FACTOID


@pytest.mark.asyncio()
async def test_resolver_uses_cheap_binding_when_present() -> None:
    """When ``list_bindings(purpose='llm_factoid')`` returns a row,
    ``resolve_runtime`` must use it directly (no fallback hop)."""
    cheap_provider, cheap_model, cheap_binding = _row_factory(
        purpose=DEFAULT_LLM_PURPOSE_FACTOID,
    )

    repo = MagicMock()

    list_calls: list[str] = []

    async def _list_bindings(*, record_tenant_id, record_bot_id, purpose=None, active_only=True):  # noqa: ARG001
        list_calls.append(purpose)
        if purpose == DEFAULT_LLM_PURPOSE_FACTOID:
            return [cheap_binding]
        return []

    repo.list_bindings = AsyncMock(side_effect=_list_bindings)
    repo.get_model = AsyncMock(return_value=cheap_model)
    repo.get_provider = AsyncMock(return_value=cheap_provider)

    svc = _build_service_with_repo(repo)

    cfg = await svc.resolve_runtime(
        record_tenant_id=cheap_binding.record_tenant_id,
        record_bot_id=cheap_binding.record_bot_id,
        purpose=DEFAULT_LLM_PURPOSE_FACTOID,
    )

    # Single lookup — no fallback.
    assert list_calls == [DEFAULT_LLM_PURPOSE_FACTOID]
    assert cfg.binding_id == cheap_binding.id
    assert cfg.model_row_id == cheap_model.id
    assert cfg.purpose == DEFAULT_LLM_PURPOSE_FACTOID


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
