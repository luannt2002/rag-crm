"""Vector store strategy registry — DI factory based on config provider name.

Pattern: caller (``bootstrap.Container``) reads ``vector_store_provider`` from
``system_config`` (Redis-cached) and asks the registry for the matching
vector store implementation. Adding a new vector backend = drop a new file
in this package and register it here; **no edits to bootstrap or query_graph**.

Default = ``"pgvector"`` (DEFAULT_VECTOR_STORE_PROVIDER). The strategy is
deliberately fail-soft on unknown provider strings so a typo in
``system_config`` cannot crash boot; instead we log + fall back to
:class:`NullVectorStore` and the request continues with empty retrieval so
the misconfig stays observable.

Mirrors :mod:`ragbot.infrastructure.reranker.registry` (single-source pattern).
"""

from __future__ import annotations

import inspect
from typing import Any

import structlog

from ragbot.infrastructure.vector.null_vector_store import NullVectorStore
from ragbot.infrastructure.vector.pgvector_store import PgVectorStore

logger = structlog.get_logger(__name__)


# Registered providers. Values are classes (not instances) so each call to
# ``build_vector_store`` returns a fresh adapter — callers stash a Singleton
# wrapper in the DI container for process-wide reuse.
#
# ``postgres`` is a friendly alias for ``pgvector`` (matches typical
# ai_providers.code conventions where a tenant may set
# ``vector_store_provider = "postgres"`` interchangeably).
_REGISTRY: dict[str, type] = {
    "pgvector": PgVectorStore,
    "postgres": PgVectorStore,  # alias — same impl
    "null": NullVectorStore,
}


def build_vector_store(provider: str | None = None, **kwargs: Any) -> Any:
    """Construct the vector store matching ``provider``.

    @param provider: registry key (``"pgvector"`` | ``"postgres"`` |
        ``"null"``). ``None`` / unknown / empty falls back to
        :class:`NullVectorStore` (warned).
    @param kwargs: forwarded to the strategy constructor (e.g.
        ``session_factory=``, ``dimension=``). Filtered to the constructor
        signature so a globally-passed kwarg does not blow up
        :class:`NullVectorStore` (which accepts ``**_``).
    @return: vector store instance mirroring :class:`PgVectorStore`'s method
        contract (``upsert_chunks`` / ``delete_by_document`` / ``search`` /
        ``hybrid_search`` / ``count`` / ``health_check`` / ``close``).
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "vector_store_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullVectorStore
    try:
        # Strategies vary in accepted kwargs (PgVectorStore needs
        # session_factory + dimension, NullVectorStore accepts **_). Filter
        # to what the constructor signature actually accepts so a globally-
        # passed kwarg does not blow up the strategy. NullVectorStore
        # declares ``**_`` so its signature.parameters keeps a VAR_KEYWORD
        # entry — inspect treats VAR_KEYWORD as accept-all and we keep that
        # behaviour explicit below.
        sig = inspect.signature(cls.__init__)
        sig_params = set(sig.parameters)
        has_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if has_var_kw:
            filtered = dict(kwargs)
        else:
            filtered = {k: v for k, v in kwargs.items() if k in sig_params}
        return cls(**filtered)
    except (TypeError, ValueError) as exc:
        logger.error(
            "vector_store_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullVectorStore()


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_vector_store", "list_providers"]
