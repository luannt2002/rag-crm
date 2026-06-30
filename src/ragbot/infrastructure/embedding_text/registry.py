"""Embedding-text strategy registry.

Pattern mirrors ``infrastructure/reranker/registry.py``: caller passes the
``provider`` string (from ``system_config`` / ``plan_limits``); registry
returns the matching strategy instance. Unknown keys fall back to
``NullEmbeddingTextStrategy`` (pass-through prefix+raw) with a warn log —
never raise, never crash boot.

Adding a new strategy = drop a file in this package + register it here.
No edits to ``DocumentService.ingest`` or ``bootstrap.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.infrastructure.embedding_text.field_selective_strategy import (
    FieldSelectiveStrategy,
)
from ragbot.infrastructure.embedding_text.null_embedding_text_strategy import (
    NullEmbeddingTextStrategy,
)
from ragbot.infrastructure.embedding_text.prefix_plus_raw_strategy import (
    PrefixPlusRawStrategy,
)
from ragbot.infrastructure.embedding_text.raw_only_strategy import RawOnlyStrategy

if TYPE_CHECKING:
    from ragbot.application.ports.embedding_text_port import EmbeddingTextStrategyPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "prefix_plus_raw": PrefixPlusRawStrategy,
    "raw_only": RawOnlyStrategy,
    "field_selective": FieldSelectiveStrategy,
    "null": NullEmbeddingTextStrategy,
}


def build_embedding_text_strategy(
    provider: str | None = None,
) -> "EmbeddingTextStrategyPort":
    """Construct the embedding-text strategy matching ``provider``.

    @param provider: registry key (``"prefix_plus_raw"`` | ``"raw_only"`` |
        ``"null"``). ``None`` / unknown / empty falls back to ``Null`` and
        emits a warn log so the misconfig is observable.
    @return: ``EmbeddingTextStrategyPort`` instance.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "embedding_text_strategy_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullEmbeddingTextStrategy
    return cls()  # type: ignore[return-value]


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_embedding_text_strategy", "list_providers"]
