"""Narrate strategy registry — DI factory keyed on config provider name.

Pattern mirrors ``infrastructure.hyde.registry``: the DI container reads
``narrate_provider`` from ``system_config`` (Redis-cached) and asks the
registry for the matching ``NarrateServicePort`` implementation. Adding
a new provider = drop a new file in this package and register it here;
**no edits to ingest pipeline** — admin wiring binds the strategy when
the operator opts in.

Default = ``"null"`` (``NullNarrateGenerator``). Unknown provider strings
raise ``ValueError`` — narration is opt-in, so a typo at the DB layer
must surface loudly rather than silently fall back.
"""

from __future__ import annotations

from typing import Any

from ragbot.application.ports.narrate_port import NarrateServicePort
from ragbot.infrastructure.narrate.llm_narrate import LLMNarrateGenerator
from ragbot.infrastructure.narrate.null_narrate import NullNarrateGenerator

_REGISTRY: dict[str, type[NarrateServicePort]] = {
    "null": NullNarrateGenerator,
    "llm": LLMNarrateGenerator,
}


def build_narrate(provider: str, **kwargs: Any) -> NarrateServicePort:
    """Construct the Narrate strategy matching ``provider``.

    @param provider: registry key (``"null"`` | ``"llm"``).
    @param kwargs: forwarded to the strategy constructor (``llm=``,
        ``spec=``, ``record_tenant_id=``, ``trace_id=`` for ``"llm"``;
        ignored for ``"null"``).
    @return: ``NarrateServicePort`` instance.
    @raise ValueError: unknown provider key — owner-opt-in surfaces loud,
        not silent fallback.
    """
    key = (provider or "").strip().lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"unknown narrate provider: {provider!r}; "
            f"registered={sorted(_REGISTRY.keys())}"
        )
    instance: NarrateServicePort = cls(**kwargs)
    return instance


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_narrate", "list_providers"]
