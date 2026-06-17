"""Retrieval-fallback strategy registry — DI factory keyed by provider string.

Pattern mirrors ``infrastructure/reranker/registry.py``:
- ``_REGISTRY`` maps stage-name string -> Strategy class.
- ``build_retrieval_fallback(name, **kwargs)`` constructs the matching
  Strategy, falling back to ``NullRetrievalStage`` on unknown names.
- ``list_stages()`` returns the registered keys (sorted).

The four built-in stages are registered eagerly; adding a 5th stage =
drop a new file in this package and add one line below.

Unknown / typo stage names degrade to ``NullRetrievalStage`` (silent
pass-through) so a misconfig in ``system_config`` cannot crash boot;
the chain simply has one fewer effective stage.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import structlog

from ragbot.infrastructure.retrieval_fallback.bm25_only_stage2 import (
    BM25OnlyStage2Retriever,
)
from ragbot.infrastructure.retrieval_fallback.hybrid_stage1 import (
    HybridStage1Retriever,
)
from ragbot.infrastructure.retrieval_fallback.keyword_stage3 import (
    KeywordStage3Retriever,
)
from ragbot.infrastructure.retrieval_fallback.null_stage import NullRetrievalStage
from ragbot.infrastructure.retrieval_fallback.parent_expand_stage4 import (
    ParentExpandStage4Retriever,
)

if TYPE_CHECKING:
    from ragbot.application.ports.retrieval_fallback_port import RetrievalFallbackPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "hybrid_stage1": HybridStage1Retriever,
    "bm25_only_stage2": BM25OnlyStage2Retriever,
    "keyword_stage3": KeywordStage3Retriever,
    "parent_expand_stage4": ParentExpandStage4Retriever,
    "null": NullRetrievalStage,
}


def build_retrieval_fallback(
    name: str | None = None,
    **kwargs: Any,
) -> "RetrievalFallbackPort":
    """Construct the retrieval-fallback strategy matching ``name``.

    @param name: registry key. ``None`` / unknown / empty falls back to
        ``NullRetrievalStage`` and emits a warning so the misconfig is
        observable.
    @param kwargs: forwarded to the strategy constructor (filtered to
        the ``__init__`` signature so a globally-passed kwarg doesn't
        crash a strategy whose constructor doesn't accept it).
    """
    key = (name or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "retrieval_fallback_unknown_provider_fallback_null",
            requested=name,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullRetrievalStage
    try:
        sig_params = set(inspect.signature(cls.__init__).parameters)
        # All stages accept **kwargs, so we forward everything; still
        # filter for stages with explicit kwargs declared.
        if "kwargs" in sig_params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(cls.__init__).parameters.values()
        ):
            filtered: dict[str, Any] = kwargs
        else:
            filtered = {k: v for k, v in kwargs.items() if k in sig_params}
        return cls(**filtered)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        logger.error(
            "retrieval_fallback_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullRetrievalStage()


def list_stages() -> list[str]:
    """Return registered stage names sorted (stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_retrieval_fallback", "list_stages"]
