"""Entity extractor strategy registry — DI factory based on provider key.

Pattern mirrors :mod:`ragbot.infrastructure.text_normalizer.registry` and
:mod:`ragbot.infrastructure.reranker.registry`.

Default = ``"null"`` (NullExtractor) — operator-OFF baseline. The strategy
is deliberately fail-soft on unknown provider strings so a typo in
``system_config`` cannot crash boot; instead we log and fall back to null
so the multi-query path keeps its existing paraphrase-only behaviour.

Adding a new language strategy = drop a new file in this package and
register it in ``_REGISTRY``; **no edits to query_graph or bootstrap**.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.infrastructure.entity_extractor.en_simple_extractor import (
    EnSimpleExtractor,
)
from ragbot.infrastructure.entity_extractor.null_extractor import NullExtractor
from ragbot.infrastructure.entity_extractor.vi_underthesea_extractor import (
    ViUnderthesseaExtractor,
)

if TYPE_CHECKING:
    from ragbot.application.ports.entity_extractor_port import EntityExtractorPort

logger = structlog.get_logger(__name__)


# Registered providers. Keep values as classes — ``build_entity_extractor``
# returns a fresh instance each call so a Singleton wrapper at the
# DI-container layer is the single shared instance per process.
_REGISTRY: dict[str, type] = {
    "null": NullExtractor,
    "vi_underthesea": ViUnderthesseaExtractor,
    "en_simple": EnSimpleExtractor,
}


def build_entity_extractor(
    provider: str | None = None,
    **kwargs,
) -> "EntityExtractorPort":
    """Construct the entity extractor matching ``provider``.

    @param provider: registry key (``"null"`` | ``"vi_underthesea"`` |
        ``"en_simple"``). ``None`` / unknown / empty falls back to
        ``NullExtractor`` (warned).
    @param kwargs: forwarded to the strategy constructor (currently
        none accept any). Filtered defensively so a globally-passed
        kwarg doesn't break a stricter constructor.
    @return: ``EntityExtractorPort`` instance.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "entity_extractor_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullExtractor
    try:
        # Filter kwargs to only those the constructor accepts.
        # All current strategies accept ``**_`` so this is mostly a
        # safety harness for future strategies with stricter signatures.
        import inspect

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
            "entity_extractor_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullExtractor()


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_entity_extractor", "list_providers"]
