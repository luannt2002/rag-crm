# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: sentence_similarity infra never wired.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """Sentence-similarity strategy registry — DI factory.

# Caller (DI container, document service, ingestion pipeline) reads the
# ``sentence_similarity_provider`` (or the higher-level
# ``embedding_semantic_chunk_enabled`` feature flag) from ``system_config``
# and asks this registry for the matching :class:`SentenceSimilarityPort`.

# Default = ``"lexical"`` — the SequenceMatcher + Jaccard blend preserves
# the historical chunking behaviour bit-for-bit when the operator has not
# opted in to embedding-based chunking. Unknown provider strings raise
# :class:`ValueError` so a typo in ``system_config`` is loud at boot.
# """

# from __future__ import annotations

# import inspect
# from typing import Any

# from ragbot.application.ports.sentence_similarity_port import SentenceSimilarityPort
# from ragbot.infrastructure.sentence_similarity.embedding_sentence_similarity import (
#     EmbeddingSentenceSimilarity,
# )
# from ragbot.infrastructure.sentence_similarity.null_sentence_similarity import (
#     NullSentenceSimilarity,
# )
# from ragbot.shared.sentence_similarity import LexicalSentenceSimilarity

# _REGISTRY: dict[str, type] = {
#     "lexical": LexicalSentenceSimilarity,
#     "embedding": EmbeddingSentenceSimilarity,
#     "null": NullSentenceSimilarity,
# }


# def build_sentence_similarity(
#     provider: str | None = None,
#     **kwargs: Any,
# ) -> SentenceSimilarityPort:
#     """Construct the sentence-similarity strategy matching ``provider``.

#     @param provider: registry key (``"lexical"`` | ``"embedding"`` |
#         ``"null"``). ``None`` / empty falls back to ``"lexical"`` so a
#         blank ``system_config`` row never breaks ingest.
#     @param kwargs: forwarded to the strategy constructor (e.g.
#         ``embedder=``, ``redis_client=``, ``cache_ttl_s=``). Kwargs the
#         constructor does not accept are filtered out — keeps the DI
#         wiring loose without forcing every adapter to swallow ``**kwargs``.
#     @return: :class:`SentenceSimilarityPort` instance.
#     @raise ValueError: when ``provider`` is non-empty and not registered.
#     """
#     key = (provider or "").strip().lower() or "lexical"
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown sentence_similarity provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}",
#         )
#     sig_params = set(inspect.signature(cls).parameters)
#     filtered = {k: v for k, v in kwargs.items() if k in sig_params}
#     return cls(**filtered)


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_sentence_similarity", "list_providers"]
