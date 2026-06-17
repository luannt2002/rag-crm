"""Verify ProviderRow.code drives LiteLLM routing + cache_control match."""

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
    _CachedBindings,
)
from ragbot.infrastructure.repositories.ai_config_repository import _to_provider


def _make_row(*, name: str, code: str | None) -> MagicMock:
    r = MagicMock()
    r.id = uuid4()
    r.name = name
    r.code = code
    r.type = "llm"
    r.base_url = "https://api.example.com"
    r.auth_type = "api_key"
    r.metadata_json = {}
    r.enabled = True
    return r


def test_provider_row_has_code_field():
    """ProviderRow must expose `code` for LiteLLM routing."""
    row = ProviderRow(
        id=uuid4(),
        name="Display Name",
        code="display_name",
        type="llm",
        base_url="https://x",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
    )
    assert row.code == "display_name"


def test_to_provider_reads_code_column():
    """_to_provider must use AIProviderModel.code (machine slug)."""
    r = _make_row(name="Jina AI", code="jina")
    provider = _to_provider(r)
    assert provider.code == "jina"
    assert provider.name == "Jina AI"


def test_to_provider_falls_back_to_lowercased_name_when_code_empty():
    """Legacy rows with NULL/empty code must fall back to lowercased name."""
    r_null = _make_row(name="Anthropic", code=None)
    assert _to_provider(r_null).code == "anthropic"

    r_empty = _make_row(name="OpenAI", code="")
    assert _to_provider(r_empty).code == "openai"

    r_whitespace = _make_row(name="Cohere", code="   ")
    assert _to_provider(r_whitespace).code == "cohere"


def test_litellm_name_uses_code_not_display_name():
    """litellm_name must compose from provider.code (machine slug), not name."""
    r = _make_row(name="Jina AI", code="jina")
    provider = _to_provider(r)
    litellm_name = f"{provider.code}/jina-reranker-v3"
    assert litellm_name == "jina/jina-reranker-v3"
    assert " " not in litellm_name


def _spec_resolver(*, provider_code: str, provider_name: str, model_kind: str, purpose: str):
    """Build a ModelResolverService primed with a single binding for a kind."""
    provider_id = uuid4()
    model_id = uuid4()
    binding_id = uuid4()
    record_bot_id = uuid4()
    record_tenant_id = uuid4()

    provider = ProviderRow(
        id=provider_id,
        name=provider_name,
        code=provider_code,
        type=model_kind,
        base_url="https://api.example.com",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
    )
    model = ModelRow(
        id=model_id,
        provider_id=provider_id,
        name=f"{provider_code}-model",
        kind=model_kind,
        context_window=8192,
        max_output_tokens=1024,
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
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
        purpose=purpose,
        model_id=model_id,
        rank=0,
        variant=None,
        weight=100,
        temperature=0.0,
        max_tokens=1024,
        top_p=1.0,
        extra_params={},
        active=True,
        version=1,
    )
    cached = _CachedBindings(
        bindings=(binding,),
        models_by_id={str(model_id): model},
        providers_by_id={str(provider_id): provider},
        cached_at=0.0,
    )

    repo = MagicMock()
    cache = MagicMock()
    clock = MagicMock()
    svc = ModelResolverService(repo=repo, cache=cache, clock=clock)
    svc._get_cached = AsyncMock(return_value=cached)  # type: ignore[method-assign]
    return svc, record_bot_id, record_tenant_id


@pytest.mark.asyncio()
async def test_reranker_spec_emits_provider_code_not_display_name():
    """RerankerSpec.provider must carry the machine code, never the display name."""
    svc, bot_id, tid = _spec_resolver(
        provider_code="jina",
        provider_name="Jina AI",
        model_kind="reranker",
        purpose="reranker",
    )
    spec = await svc.resolve_reranker(bot_id, record_tenant_id=tid)
    assert spec.provider == "jina"
    assert spec.provider != "Jina AI"
    assert " " not in spec.provider


@pytest.mark.asyncio()
async def test_embedding_spec_emits_provider_code_not_display_name():
    """EmbeddingSpec.provider must carry the machine code, never the display name."""
    svc, bot_id, tid = _spec_resolver(
        provider_code="openai",
        provider_name="OpenAI",
        model_kind="embedding",
        purpose="embedding",
    )
    spec = await svc.resolve_embedding(bot_id, record_tenant_id=tid)
    assert spec.provider == "openai"
    assert spec.provider != "OpenAI"
