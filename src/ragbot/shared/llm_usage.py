"""LLM response usage extraction — pure helper, no provider/SDK imports.

Centralised here so both the application layer (e.g.
``structured_output_helper``) and the infrastructure layer (e.g.
``dynamic_litellm_router``) can read token counts off a LiteLLM
``ModelResponse`` (or final stream chunk / plain dict) without the
application layer importing the router module.

LiteLLM exposes ``usage`` as either a ``Usage`` pydantic model or a
plain dict, depending on provider. The cached-token count lives on
``usage.prompt_tokens_details.cached_tokens`` for OpenAI auto-cache.
"""

from __future__ import annotations

from typing import Any


def _uget(obj: Any, attr: str, default: int) -> int:
    """Best-effort int read on either an object attribute or dict key."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(attr, default)
    else:
        v = getattr(obj, attr, default)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def extract_usage_from_response(resp: Any) -> tuple[int, int, int]:
    """Return ``(prompt_tokens, completion_tokens, cached_tokens)``.

    Returns zeros when usage data is absent — caller decides how to log.
    """
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    if usage is None:
        return 0, 0, 0
    prompt = _uget(usage, "prompt_tokens", 0)
    completion = _uget(usage, "completion_tokens", 0)
    details = (
        usage.get("prompt_tokens_details") if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    )
    cached = _uget(details, "cached_tokens", 0) if details else 0
    return prompt, completion, cached


__all__ = ["extract_usage_from_response"]
