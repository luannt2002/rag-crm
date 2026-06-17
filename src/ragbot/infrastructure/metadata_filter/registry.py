"""Metadata filter strategy registry — DI factory based on provider key.

Pattern mirrors :mod:`ragbot.infrastructure.entity_extractor.registry` and
:mod:`ragbot.infrastructure.reranker.registry`.

Default = ``"null"`` (NullFilter) — operator-OFF baseline. The strategy
is deliberately fail-soft on unknown provider strings so a typo in
``system_config`` cannot crash boot; instead we log and fall back to
null so the retrieval path keeps its existing unfiltered behaviour.

Adding a new strategy = drop a new file in this package and register it
in ``_REGISTRY``; **no edits to query_graph or bootstrap**.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import structlog

from ragbot.infrastructure.metadata_filter.article_aware_filter import (
    ArticleAwareFilter,
)
from ragbot.infrastructure.metadata_filter.generic_llm_extractor import (
    GenericLLMMetadataExtractor,
)
from ragbot.infrastructure.metadata_filter.null_filter import NullFilter

if TYPE_CHECKING:
    from ragbot.application.ports.metadata_filter_port import MetadataFilterPort

logger = structlog.get_logger(__name__)


# Registered providers. Keep values as classes — ``build_metadata_filter``
# returns a fresh instance each call so the DI-container layer is the single
# shared instance per process.
_REGISTRY: dict[str, type] = {
    "null": NullFilter,
    "article_aware": ArticleAwareFilter,
    # Layer 3 universal LLM-based extractor (Plan 260604-metadata-aware-v4).
    # Works for ANY bot/domain without per-bot config. Verified evidence
    # 2026-06-04: gpt-4.1-nano extract 7/7 case correct.
    "generic_llm": GenericLLMMetadataExtractor,
}


def build_metadata_filter(
    provider: str | None = None,
    **kwargs,
) -> "MetadataFilterPort":
    """Construct the metadata filter matching ``provider``.

    @param provider: registry key (``"null"`` | ``"article_aware"``).
        ``None`` / unknown / empty falls back to :class:`NullFilter`
        (warned).
    @param kwargs: forwarded to the strategy constructor (currently
        ``patterns: list[dict]`` is honoured by ArticleAwareFilter).
        Filtered defensively so a globally-passed kwarg cannot break a
        stricter constructor.
    @return: :class:`MetadataFilterPort` instance.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "metadata_filter_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullFilter
    try:
        sig_params = set(inspect.signature(cls.__init__).parameters)
        if "kwargs" in sig_params or any(
            p.kind.name == "VAR_KEYWORD"
            for p in inspect.signature(cls.__init__).parameters.values()
        ):
            filtered = kwargs
        else:
            filtered = {k: v for k, v in kwargs.items() if k in sig_params}
        return cls(**filtered)  # type: ignore[return-value]
    except (NotImplementedError, TypeError, ValueError) as exc:
        logger.error(
            "metadata_filter_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullFilter()


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_metadata_filter", "list_providers"]
