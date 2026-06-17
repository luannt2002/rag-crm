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

# """Sentence-split multi-vector embedder — simple late-interaction strategy.

# For each chunk, split into sentences and embed each sentence as an
# independent role-tagged vector. Domain-neutral splitter — terminates on
# ``. ? !`` followed by whitespace or end of string, falls back to the full
# chunk when no sentence boundary is found. Vietnamese / English text both
# covered by the simple regex; richer language-aware splitters are future
# work.

# Caller contract::

#     embedder = SentenceSplitMultiVectorEmbedder(inner=embedder_port)
#     rows = await embedder.embed_chunk("S1. S2! S3?")
    # rows = [MultiVectorChunk(role="sentence:0", vector=...), ...]

# Operator knobs (system_config):
# * ``multi_vector_enabled`` — master switch (default False).
# * ``multi_vector_max_sentences`` — cap per chunk to bound vector storage.
# * ``multi_vector_min_sentence_chars`` — drop fragments below this length.
# """

# from __future__ import annotations

# import re
# from typing import Any

# import structlog

# from ragbot.application.ports.embedder_port import EmbedderPort
# from ragbot.application.ports.multi_vector_embed_port import (
#     MultiVectorChunk,
#     MultiVectorEmbedPort,
# )
# from ragbot.shared.constants import (
#     DEFAULT_MULTI_VECTOR_MAX_SENTENCES,
#     DEFAULT_MULTI_VECTOR_MIN_SENTENCE_CHARS,
# )
# from ragbot.shared.errors import EmbeddingError

# logger = structlog.get_logger(__name__)


# _SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
# _ROLE_PREFIX = "sentence"


# def _split_sentences(text: str, *, min_chars: int, max_sentences: int) -> list[str]:
#     """Domain-neutral sentence split with length-floor + count-cap filters."""
#     if not text:
#         return []
#     raw = [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]
#     if not raw:
        # Fall back to the trimmed full chunk so callers always see >=1 span
        # when there is non-empty input — avoids silent empty embeds.
#         trimmed = text.strip()
#         return [trimmed] if trimmed else []
#     filtered = [s for s in raw if len(s) >= min_chars] or raw[:1]
#     if max_sentences > 0:
#         filtered = filtered[:max_sentences]
#     return filtered


# class SentenceSplitMultiVectorEmbedder(MultiVectorEmbedPort):
#     """Split chunk into N sentences, embed each as a role-tagged vector."""

#     _PROVIDER_NAME = "sentence_split"

#     def __init__(
#         self,
#         *,
#         inner: EmbedderPort,
#         max_sentences: int = DEFAULT_MULTI_VECTOR_MAX_SENTENCES,
#         min_sentence_chars: int = DEFAULT_MULTI_VECTOR_MIN_SENTENCE_CHARS,
#         **_: Any,
#     ) -> None:
#         if inner is None:
#             raise ValueError("SentenceSplitMultiVectorEmbedder requires an inner EmbedderPort")
#         self._inner = inner
#         self._max_sentences = max_sentences
#         self._min_sentence_chars = min_sentence_chars

#     @property
#     def provider_name(self) -> str:
#         return self._PROVIDER_NAME

#     @property
#     def dimension(self) -> int:
#         return self._inner.dimension

#     async def embed_chunk(self, text: str) -> list[MultiVectorChunk]:
#         sentences = _split_sentences(
#             text,
#             min_chars=self._min_sentence_chars,
#             max_sentences=self._max_sentences,
#         )
#         if not sentences:
#             return []
#         try:
#             vectors = await self._inner.embed_documents(sentences)
#         except EmbeddingError:
#             raise
#         return [
#             MultiVectorChunk(role=f"{_ROLE_PREFIX}:{idx}", vector=vec)
#             for idx, vec in enumerate(vectors)
#         ]

#     async def embed_chunks(self, texts: list[str]) -> list[list[MultiVectorChunk]]:
        # Naive batching — one call per text. Avoids cross-chunk index
        # collisions in the flat batch response; the inner embedder already
        # batches at the HTTP layer for sentence lists.
#         out: list[list[MultiVectorChunk]] = []
#         for text in texts:
#             out.append(await self.embed_chunk(text))
#         return out


# __all__ = ["SentenceSplitMultiVectorEmbedder"]
