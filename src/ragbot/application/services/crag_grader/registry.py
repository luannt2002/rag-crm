"""CRAG grader strategy registry — DI factory based on provider key.

Pattern mirrors :mod:`ragbot.infrastructure.reranker.registry` and
:mod:`ragbot.infrastructure.entity_extractor.registry`.

Default = ``"per_chunk"`` (legacy N-call grader) — flipping the new
abstraction layer on a deployment that already shipped the inline
batch-grade path therefore introduces **zero behaviour change** until
an operator updates ``system_config.crag_grader_provider`` to
``"batch"`` (or ``"null"`` for emergency disable).

Fail-soft: an unknown / typo provider key falls back to
:class:`PerChunkCragGrader` (the legacy default) and logs a warning so
the misconfig is observable but does not crash boot. The same applies
to constructor failure (e.g. caller forgot ``structured_llm_caller``)
— the registry catches and downgrades to a :class:`NullCragGrader`
instance so the pipeline still produces a valid score dict.

Adding a new strategy = drop a file + add one row to ``_REGISTRY``;
**no edits required to query_graph or bootstrap**.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import structlog

from ragbot.application.services.crag_grader.batch_grader import BatchCragGrader
from ragbot.application.services.crag_grader.null_grader import NullCragGrader
from ragbot.application.services.crag_grader.per_chunk_grader import (
    PerChunkCragGrader,
)

if TYPE_CHECKING:
    from ragbot.application.ports.crag_grader_port import CragGraderPort

logger = structlog.get_logger(__name__)


# Registered providers. Values are classes; ``build_crag_grader``
# returns a fresh instance per call so a Singleton wrapper at the DI
# layer is the single shared instance per process when desired.
_REGISTRY: dict[str, type] = {
    "null": NullCragGrader,
    "per_chunk": PerChunkCragGrader,
    "batch": BatchCragGrader,
}


def build_crag_grader(
    provider: str | None = None,
    **kwargs,
) -> "CragGraderPort":
    """Construct the CRAG grader matching ``provider``.

    @param provider: registry key (``"null"`` | ``"per_chunk"`` |
        ``"batch"``). ``None`` / unknown / empty falls back to
        ``PerChunkCragGrader`` (the legacy default — preserves prior
        behaviour on a deployment that flipped the new wire on without
        also updating ``system_config``).
    @param kwargs: forwarded to the strategy constructor. Filtered
        defensively via :func:`inspect.signature` so a globally-passed
        kwarg cannot blow up a stricter constructor (NullCragGrader
        ignores everything; Batch / PerChunk require
        ``structured_llm_caller`` + ``system_prompt``).
    @return: ``CragGraderPort`` instance — never raises; falls back to
        ``NullCragGrader`` if construction would throw so the orchestrator
        always receives a usable grader.
    """
    key = (provider or "").strip().lower() or "per_chunk"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "crag_grader_unknown_provider_fallback_per_chunk",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = PerChunkCragGrader
    try:
        sig = inspect.signature(cls.__init__)
        params = sig.parameters
        has_var_kw = any(
            p.kind.name == "VAR_KEYWORD" for p in params.values()
        )
        if has_var_kw:
            filtered = kwargs
        else:
            filtered = {k: v for k, v in kwargs.items() if k in params}
        return cls(**filtered)  # type: ignore[return-value]
    except (NotImplementedError, TypeError, ValueError) as exc:
        # Constructor signature mismatch (missing required kwarg) — the
        # safe default is NullCragGrader (every chunk scored 1.0). The
        # alternative would be to fall back to PerChunkCragGrader, but
        # PerChunk also needs ``structured_llm_caller`` so the same
        # TypeError would re-fire. Null is the only strategy that
        # constructs with no required args.
        logger.error(
            "crag_grader_strategy_init_failed_fallback_null",
            requested=key,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return NullCragGrader()


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_crag_grader", "list_providers"]
