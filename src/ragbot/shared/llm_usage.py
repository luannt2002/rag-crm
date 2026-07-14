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

import functools
from typing import Any


@functools.lru_cache(maxsize=1)
def _token_encoder() -> Any:
    """Lazily build a provider-agnostic BPE encoder (tiktoken cl100k_base).

    Returns None when tiktoken is unavailable so callers degrade to no-estimate.
    """
    try:
        import tiktoken  # noqa: PLC0415 — optional dep, lazy

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 — tokenizer is optional; no-estimate fallback
        return None


def _text_of(content: Any) -> str:
    """Best-effort text of a chat ``message["content"]`` (str or multimodal list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", "")) if isinstance(p, dict) else str(p)
            for p in content
        )
    return "" if content is None else str(content)


def estimate_tokens_fallback(
    messages: Any,
    completion_text: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[int, int]:
    """Fill a MISSING (zero) token count with a local tiktoken estimate.

    Some upstream proxies (e.g. certain LLM gateways) omit the ``usage`` block, so
    the provider returns 0 tokens and cost logs as $0 — unmeasurable. This
    estimates the count locally from the prompt messages + completion text. It is
    an ESTIMATE (a generic BPE, not the model's exact tokenizer — ~±5-15% for
    non-OpenAI models) that turns "always $0" into a usable cost-audit figure. A
    REAL provider count is never overwritten (only a 0 is filled).
    """
    if prompt_tokens > 0 and completion_tokens > 0:
        return prompt_tokens, completion_tokens
    enc = _token_encoder()
    if enc is None:
        return prompt_tokens, completion_tokens
    if prompt_tokens == 0 and messages:
        prompt_text = "\n".join(_text_of(m.get("content")) for m in messages)
        prompt_tokens = len(enc.encode(prompt_text))
    if completion_tokens == 0 and completion_text:
        completion_tokens = len(enc.encode(completion_text))
    return prompt_tokens, completion_tokens


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


__all__ = ["estimate_tokens_fallback", "extract_usage_from_response"]
