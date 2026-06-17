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
# Reason: Multi-vector stack never plumbed into bootstrap or graph.
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

# """Null multi-vector embedder — preserves single-vector retrieval semantics.

# Default operator selection for the multi-vector port. Wraps an underlying
# single-vector :class:`EmbedderPort` and emits exactly ONE
# :class:`MultiVectorChunk` per input text (role ``"chunk"``), so callers that
# opt-in to the multi-vector retrieval surface see the same scoring behaviour
# as before the feature flag was introduced.

# If no inner embedder is provided the adapter degrades to an empty vector
# list — the late-interaction node in the orchestrator falls back to the
# single-embedding path via the existing retrieval gates.
# """

# from __future__ import annotations

# from typing import Any

# import structlog

# from ragbot.application.ports.embedder_port import EmbedderPort
# from ragbot.application.ports.multi_vector_embed_port import (
#     MultiVectorChunk,
#     MultiVectorEmbedPort,
# )
# from ragbot.shared.errors import EmbeddingError

# logger = structlog.get_logger(__name__)


# _NULL_ROLE = "chunk"


# class NullMultiVectorEmbedder(MultiVectorEmbedPort):
#     """Single-vector passthrough — emits one ``MultiVectorChunk`` per chunk."""

#     _PROVIDER_NAME = "null"

#     def __init__(self, *, inner: EmbedderPort | None = None, **_: Any) -> None:
        # ``inner`` may be omitted so the registry's fail-soft fallback can
        # construct the null adapter without dragging a full embedder graph
        # into unit tests. Empty input then yields ``[]`` per the port contract.
#         self._inner = inner

#     @property
#     def provider_name(self) -> str:
#         return self._PROVIDER_NAME

#     @property
#     def dimension(self) -> int:
        # Surface inner dimension when wired so callers can size buffers;
        # 0 when running headless (no inner embedder).
#         return self._inner.dimension if self._inner is not None else 0

#     async def embed_chunk(self, text: str) -> list[MultiVectorChunk]:
#         if not text:
#             return []
#         if self._inner is None:
            # Degrade silently — no inner embedder means the multi-vector
            # layer cannot produce a vector, but it MUST NOT crash retrieval.
#             return []
#         try:
#             vector = await self._inner.embed_query(text)
#         except EmbeddingError:
            # Match EmbedderPort failure semantics — propagate so the caller
            # can route to the single-embedding fallback path.
#             raise
#         return [MultiVectorChunk(role=_NULL_ROLE, vector=vector)]

#     async def embed_chunks(self, texts: list[str]) -> list[list[MultiVectorChunk]]:
#         if not texts:
#             return []
#         if self._inner is None:
#             return [[] for _ in texts]
#         vectors = await self._inner.embed_documents(texts)
#         return [
#             [MultiVectorChunk(role=_NULL_ROLE, vector=vec)] if text else []
#             for text, vec in zip(texts, vectors, strict=True)
#         ]


# __all__ = ["NullMultiVectorEmbedder"]
