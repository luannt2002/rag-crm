"""``format_litellm_model`` helper — DI-friendly provider prefix policy.

Pins (Agent R Task R.2 — requires_prefix DI):

The "prepend provider code" rule was previously hard-coded as ``if p.code ==
"openai": skip; else prefix``. That was a Strategy + DI violation (per-brand
literal inside the orchestrator). The replacement reads ``requires_prefix``
from the ``ai_providers`` row (DB column, migration 010e) and lets the bot
owner control prefix behaviour without a code edit.

Cases:

1. ``requires_prefix=False`` (OpenAI / Anthropic / OpenAI-compatible) → return
   model name untouched.
2. ``requires_prefix=True`` (Cohere / Jina / Voyage / ZeroEntropy / …) →
   return ``{provider.code}/{model_name}``.
3. Already-prefixed model name (``vertex_ai/gemini-1.5-pro``) → passthrough
   regardless of ``requires_prefix`` (LiteLLM treats explicit prefix as
   authoritative).
4. Default ``requires_prefix=True`` when row was loaded pre-migration (legacy
   safety — LiteLLM convention).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from ragbot.application.ports.ai_config_port import ProviderRow
from ragbot.application.services.model_resolver import format_litellm_model


def _provider(code: str, *, requires_prefix: bool = True) -> ProviderRow:
    return ProviderRow(
        id=uuid4(),
        name=code,
        code=code,
        type="llm",
        base_url="https://api.example.invalid",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
        metadata={},
        requires_prefix=requires_prefix,
    )


def test_openai_no_prefix_when_requires_prefix_false() -> None:
    """OpenAI: ``ai_providers.requires_prefix=false`` → model name untouched."""
    p = _provider("openai", requires_prefix=False)
    assert format_litellm_model("gpt-4.1-mini", p) == "gpt-4.1-mini"


def test_anthropic_no_prefix_when_requires_prefix_false() -> None:
    """Anthropic native API — LiteLLM accepts unprefixed model names."""
    p = _provider("anthropic", requires_prefix=False)
    assert (
        format_litellm_model("claude-haiku-4-5", p)
        == "claude-haiku-4-5"
    )


def test_cohere_prefixed_when_requires_prefix_true() -> None:
    """Cohere / generic provider requires the ``cohere/`` LiteLLM prefix."""
    p = _provider("cohere", requires_prefix=True)
    assert format_litellm_model("rerank-v3.5", p) == "cohere/rerank-v3.5"


def test_already_prefixed_model_passes_through() -> None:
    """If caller pre-prefixed (vertex_ai/…), do not double-prefix."""
    p = _provider("vertex_ai", requires_prefix=True)
    assert (
        format_litellm_model("vertex_ai/gemini-1.5-pro", p)
        == "vertex_ai/gemini-1.5-pro"
    )
    # Also true for a provider with requires_prefix=False that happens to
    # receive an explicitly-prefixed name from a binding extra_params override.
    p2 = _provider("openai", requires_prefix=False)
    assert (
        format_litellm_model("azure/my-deployment", p2)
        == "azure/my-deployment"
    )


def test_default_requires_prefix_is_true() -> None:
    """ProviderRow dataclass default = True (legacy safety / LiteLLM convention)."""
    row = ProviderRow(
        id=uuid4(),
        name="some_new_provider",
        code="some_new_provider",
        type="llm",
        base_url="https://api.example.invalid",
        auth_type="api_key",
        credentials_vault_path=None,
        enabled=True,
        # requires_prefix omitted → must default True
    )
    assert row.requires_prefix is True
    assert (
        format_litellm_model("model-x", row)
        == "some_new_provider/model-x"
    )
