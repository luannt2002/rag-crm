"""Lexical retrieval strategy registry — DI factory keyed by provider name.

Pattern mirrors ``infrastructure/reranker/registry.py`` /
``infrastructure/rate_limiter/registry.py``: the caller
(``bootstrap.Container``) reads ``lexical_retrieval_provider`` from
``system_config`` (Redis-cached) and asks the registry for the matching
``LexicalRetrievalPort`` implementation. Adding a new provider (e.g.
Elasticsearch, OpenSearch) = drop a new file in this package and add a
single registry entry; **no edits to query_graph or bootstrap**.

Default = ``"null"`` (NullLexicalRetrieval) — operator-OFF baseline for
backward compatibility. Unknown / typo provider strings fall back to
null with a structured warn log so a config typo cannot crash boot.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import structlog

from ragbot.infrastructure.retrieval.null_lexical_retrieval import NullLexicalRetrieval
from ragbot.infrastructure.retrieval.pg_bm25_retrieval import PgBM25Retrieval

if TYPE_CHECKING:
    from ragbot.application.ports.lexical_retrieval_port import LexicalRetrievalPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "pg_textsearch": PgBM25Retrieval,
    "null": NullLexicalRetrieval,
}


def build_lexical_retrieval(
    provider: str | None = None,
    **kwargs: Any,
) -> "LexicalRetrievalPort":
    """Construct the lexical retrieval adapter matching ``provider``.

    @param provider: registry key (``"pg_textsearch"`` | ``"null"``).
        ``None`` / unknown / empty falls back to ``NullLexicalRetrieval``
        with a warn log (config-typo guard).
    @param kwargs: forwarded to the strategy constructor (filtered to the
        constructor signature so a globally-passed kwarg cannot blow up
        the null path which accepts arbitrary kwargs but the BM25 path
        is strict about ``session_factory``).
    @return: ``LexicalRetrievalPort`` instance.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "lexical_retrieval_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullLexicalRetrieval
    sig_params = set(inspect.signature(cls.__init__).parameters)
    # Accept-all-kwargs adapters (e.g. NullLexicalRetrieval via **kwargs)
    # report no named params but still need the values; only filter when
    # the constructor enumerates positional/keyword names.
    if "kwargs" in sig_params or "kwds" in sig_params:
        filtered = dict(kwargs)
    else:
        filtered = {k: v for k, v in kwargs.items() if k in sig_params}
    try:
        return cls(**filtered)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        logger.error(
            "lexical_retrieval_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullLexicalRetrieval()


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ("build_lexical_retrieval", "list_providers")
