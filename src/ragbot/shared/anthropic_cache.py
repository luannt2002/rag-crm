"""Anthropic prompt-cache helper — shared utility usable by both
application services and infrastructure adapters without crossing
layer boundaries.

The actual implementation continues to live in the LiteLLM router
(it owns the provider-detection helpers); this module re-exports a
public name so cross-layer callers don't reach into a private symbol.
"""

from __future__ import annotations

from typing import Any

from ragbot.infrastructure.llm.dynamic_litellm_router import (
    _apply_anthropic_cache_control as _impl,
)


def apply_anthropic_cache_control(
    messages: list[dict[str, Any]],
    *,
    litellm_name: str | None = None,
    provider_code: str | None = None,
) -> list[dict[str, Any]]:
    """Mark the first system message with Anthropic ephemeral cache control.

    No-op for non-Anthropic providers. See router implementation for
    breakpoint semantics + idempotency contract.
    """
    return _impl(messages, litellm_name=litellm_name, provider_code=provider_code)


__all__ = ["apply_anthropic_cache_control"]
