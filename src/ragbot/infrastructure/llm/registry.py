"""LLM provider registry — Strategy + DI pattern enforcement.

Per ``CLAUDE.md`` Strategy + DI mindset: orchestration code never
branches on ``if provider == "openai" ...``. Instead, the registry maps
a config string ("dynamic_litellm" / "speculative") to the class that
implements ``LLMPort``. ``build_llm(provider=...)`` looks the class up
and constructs it with the kwargs the caller provides.

Adding a new race / routing strategy is a new file under
``infrastructure/llm/`` + one registry entry. ``query_graph`` /
``bootstrap`` stay untouched.
"""

from __future__ import annotations

from typing import Any

from ragbot.application.ports.llm_port import LLMPort
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.infrastructure.llm.speculative_router import SpeculativeRouter


_REGISTRY: dict[str, type[LLMPort]] = {
    "dynamic_litellm": DynamicLiteLLMRouter,
    "speculative": SpeculativeRouter,
}


def build_llm(*, provider: str, **kwargs: Any) -> LLMPort:
    """Look ``provider`` up in the registry and construct the impl.

    @raises KeyError: when ``provider`` is not registered. We do NOT
        fall back to a default — surfacing the misconfig early prevents
        silent routing to the wrong model.
    """
    try:
        cls = _REGISTRY[provider]
    except KeyError as exc:
        raise KeyError(
            f"unknown llm provider {provider!r}; registered: {sorted(_REGISTRY)}",
        ) from exc
    return cls(**kwargs)  # type: ignore[call-arg]


def register_llm(provider: str, cls: type[LLMPort]) -> None:
    """Test-only: register a stub LLMPort under ``provider``.

    Production registration happens at module import (above). Tests use
    this to inject a Mock-based LLMPort under a synthetic provider key.
    """
    _REGISTRY[provider] = cls


__all__ = ["build_llm", "register_llm"]
