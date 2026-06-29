# ============================================================
# DISABLED — UNUSED (commented-marker, NOT deleted)
# ============================================================
# The LLM/rule chunking-strategy SELECTOR (AdapChunk Tang-4 Port) has
# ZERO runtime callers: ``chunking_strategy_provider`` is read by nothing;
# strategy routing is done by the deterministic profile router
# (shared/chunking/analyze.select_strategy + apply_cross_check).
# WHY kept: reversible escape-hatch — remove this header to reactivate.
# Policy: disabled-by-comment, physical removal deferred to operator.
# ============================================================
"""Chunking-strategy-resolver registry — DI factory keyed on config provider.

Mirrors ``infrastructure.narrate.registry``: the DI container reads
``chunking_strategy_provider`` from ``system_config`` and asks the registry for
the matching ``ChunkingStrategyResolverPort``. Adding a provider = drop a file
+ register it here; no edits to the ingest pipeline.

Default = ``"rule"`` (deterministic, no LLM, byte-identical to today). ``"llm"``
is opt-in — the LLM selector still degrades to ``rule`` on any failure, so the
worst case is "no worse than rule", never a broken ingest.
"""
from __future__ import annotations

from typing import Any

from ragbot.application.ports.strategy_ports import ChunkingStrategyResolverPort
from ragbot.infrastructure.chunking_strategy.llm_resolver import (
    LLMChunkingStrategyResolver,
)
from ragbot.infrastructure.chunking_strategy.rule_resolver import (
    RuleChunkingStrategyResolver,
)

_REGISTRY: dict[str, type[ChunkingStrategyResolverPort]] = {
    "rule": RuleChunkingStrategyResolver,
    "null": RuleChunkingStrategyResolver,  # alias — deterministic default
    "llm": LLMChunkingStrategyResolver,
}


def build_chunking_resolver(
    provider: str, **kwargs: Any
) -> ChunkingStrategyResolverPort:
    """Construct the resolver matching ``provider``.

    @param provider: registry key (``"rule"`` | ``"null"`` | ``"llm"``).
    @param kwargs: forwarded to the constructor (``llm=``, ``spec=``,
        ``fallback=``, ``record_tenant_id=``, ``trace_id=`` for ``"llm"``;
        ignored for ``"rule"`` / ``"null"``).
    @raise ValueError: unknown provider key — surfaces loud, not silent.
    """
    key = (provider or "").strip().lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"unknown chunking_strategy provider: {provider!r}; "
            f"registered={sorted(_REGISTRY.keys())}"
        )
    return cls(**kwargs)  # type: ignore[call-arg]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = ["build_chunking_resolver", "list_providers"]
