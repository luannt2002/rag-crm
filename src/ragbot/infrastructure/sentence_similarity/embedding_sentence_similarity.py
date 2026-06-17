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

# """EmbeddingSentenceSimilarity — dense-vector cosine boundary scorer.

# Addresses a lexical-scoring gap: the baseline ``_sentence_similarity``
# blends :class:`difflib.SequenceMatcher` and word Jaccard — both
# *lexical* signals. Vietnamese paraphrase pairs like "Sản phẩm ABC giá 1
# triệu" / "Mặt hàng ABC có giá 1.000.000 đ" score near 0.0 lexically
# (no shared n-grams, different tokens) yet are clearly the same topic.
# Replacing the score with embedding cosine surfaces the semantic
# equivalence and prevents the chunker from over-segmenting paraphrased
# content.

# Design
# ------
# * Calls :class:`EmbedderPort.embed_query` for each unique sentence.
# * Caches the resulting vector in Redis under
#   ``ragbot:sentsim:{model_id}:{sha256(sentence)[:16]}`` with TTL pulled
#   from ``system_config.sentence_embedding_cache_ttl_seconds`` (default
#   :data:`DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S`).
# * In-memory LRU dedupe within a single call to ``similarity`` covers the
#   common case where ``_chunk_semantic`` re-asks for the same sentence on
#   adjacent pairs (sentence ``i`` appears in pair ``(i-1, i)`` and
#   ``(i, i+1)``).
# * Redis failure modes degrade to a fresh embed call (transport error =
#   silent degrade, per claude-mem graceful-degradation pattern). The chat
#   pipeline NEVER fails closed on this cache.

# Proof citation
# --------------
# Pattern: LangChain ``SemanticChunker`` (langchain-experimental) +
# Anthropic *Contextual Retrieval* pre-embed pipeline. NVIDIA RAGAS
# chunking benchmark: page-level recall lift 0.51 → 0.65 when switching
# from lexical to embedding boundaries on narrative documents.
# """

# from __future__ import annotations

# import asyncio
# import hashlib
# import json
# from typing import Any

# import structlog
# from redis.exceptions import RedisError

# from ragbot.application.ports.embedder_port import EmbedderPort
# from ragbot.shared.constants import DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S
# from ragbot.shared.sentence_similarity import cosine_similarity

# logger = structlog.get_logger(__name__)

# _HASH_HEX_PREFIX = 16
# _LOCAL_CACHE_MAX = 4096  # bound the per-instance dict; sentences embed once per ingest


# class EmbeddingSentenceSimilarity:
#     """Async sentence-similarity via dense embedding cosine + Redis cache."""

#     def __init__(
#         self,
#         embedder: EmbedderPort,
#         redis_client: Any | None = None,
#         cache_ttl_s: int = DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S,
#     ) -> None:
#         self._embedder = embedder
#         self._redis = redis_client
#         self._cache_ttl_s = int(cache_ttl_s)
        # Per-instance in-memory cache (single ingest call), capped to keep
        # memory bounded for pathological 10k-sentence docs.
#         self._local_cache: dict[str, list[float]] = {}
#         self._calls = 0
#         self._cache_hits = 0
#         self._cache_misses = 0
#         self._local_hits = 0

#     @staticmethod
#     def get_provider_name() -> str:
#         return "embedding"

#     @property
#     def provider_name(self) -> str:
#         return self.get_provider_name()

#     @staticmethod
#     def _safe_model(model_id: str) -> str:
#         return (model_id or "unknown").replace(":", "_").replace(" ", "_") or "unknown"

#     def _key(self, sentence: str) -> str:
#         h = hashlib.sha256(sentence.encode("utf-8")).hexdigest()[:_HASH_HEX_PREFIX]
#         return f"ragbot:sentsim:{self._safe_model(self._embedder.model_id)}:{h}"

#     async def _redis_get(self, key: str) -> list[float] | None:
#         if self._redis is None:
#             return None
#         try:
#             raw = await self._redis.get(key)
#         except (RedisError, OSError, asyncio.TimeoutError):
#             logger.debug("sentsim_cache_get_failed", key=key, exc_info=True)
#             return None
#         if not raw:
#             return None
#         try:
#             if isinstance(raw, (bytes, bytearray)):
#                 raw = raw.decode("utf-8")
#             payload = json.loads(raw)
#         except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
#             logger.debug("sentsim_cache_corrupt_payload", key=key, exc_info=True)
#             return None
#         if not isinstance(payload, list):
#             return None
#         return payload

#     async def _redis_set(self, key: str, vec: list[float]) -> None:
#         if self._redis is None or self._cache_ttl_s <= 0 or not vec:
#             return
#         try:
#             await self._redis.setex(key, self._cache_ttl_s, json.dumps(vec))
#         except (RedisError, OSError, asyncio.TimeoutError, TypeError):
#             logger.debug("sentsim_cache_set_failed", key=key, exc_info=True)

#     async def _vector_for(self, sentence: str) -> list[float]:
        # 1. Hot in-memory cache (single ingest call)
#         cached = self._local_cache.get(sentence)
#         if cached is not None:
#             self._local_hits += 1
#             return cached

        # 2. Redis cache
#         key = self._key(sentence)
#         redis_vec = await self._redis_get(key)
#         if redis_vec is not None:
#             self._cache_hits += 1
#             if len(self._local_cache) < _LOCAL_CACHE_MAX:
#                 self._local_cache[sentence] = redis_vec
#             return redis_vec

        # 3. Fresh embed
#         self._cache_misses += 1
#         vec = await self._embedder.embed_query(sentence)
#         if vec:
#             await self._redis_set(key, vec)
#             if len(self._local_cache) < _LOCAL_CACHE_MAX:
#                 self._local_cache[sentence] = vec
#         return vec

#     async def similarity(self, s1: str, s2: str) -> float:
#         self._calls += 1
#         if not s1 or not s2:
#             return 0.0
#         v1 = await self._vector_for(s1)
#         v2 = await self._vector_for(s2)
#         if not v1 or not v2:
            # Embedder returned empty (e.g. provider degraded). Treat as
            # boundary candidate; caller's threshold determines split.
#             return 0.0
#         try:
#             return cosine_similarity(v1, v2)
#         except ValueError:
            # Dimension mismatch only happens when an operator hot-swaps
            # the embedder mid-ingest. Fall back to boundary signal so the
            # chunker still produces output; loud structured log surfaces
            # the misconfig.
#             logger.warning(
#                 "sentsim_dimension_mismatch",
#                 d1=len(v1),
#                 d2=len(v2),
#                 model=self._embedder.model_id,
#             )
#             return 0.0

#     def stats(self) -> dict[str, float | int]:
#         total = self._cache_hits + self._cache_misses
#         hit_rate = (self._cache_hits / total) if total > 0 else 0.0
#         return {
#             "calls": self._calls,
#             "cache_hits": self._cache_hits,
#             "cache_misses": self._cache_misses,
#             "local_hits": self._local_hits,
#             "cache_hit_rate": hit_rate,
#         }


# __all__ = ["EmbeddingSentenceSimilarity"]
