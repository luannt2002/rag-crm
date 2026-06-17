"""Class-based Redis cache for query embedding vectors.

Wraps ``shared.embedding_cache`` (functional API) so callers in the DI
graph (orchestration nodes, retrieval pipeline) receive an injectable
object rather than module-level functions. Two benefits:

1. Tests can substitute an in-memory fake without monkey-patching the
   module — matches the Strategy + DI mindset (CLAUDE.md).
2. TTL is resolved per-call from ``system_config.embed.cache_ttl_s``
   instead of frozen at module import. Operator flip → next-request
   honour after ``bootstrap_config`` TTL window elapses.

Key layout
----------
``ragbot:embed:{safe_model}:{sha256(query)[:16]}``

* Embedding for the same text + model is identical across bots, so the
  key has no bot scope (intentional — maximises cross-bot reuse).
* Model swap is namespace-safe: a different ``model`` string produces a
  different key, so old vectors never leak under the new model name.

Failure mode
------------
Aux dependency on Redis: ``get`` returns ``None`` on any error (treated as
miss), ``set`` swallows. Embedding generation must continue when Redis is
flapping — the chat pipeline does NOT fail closed on cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

_HASH_HEX_PREFIX = 16


class EmbedCache:
    """Class-based Redis cache for query embedding vectors (model-scoped)."""

    def __init__(self, redis_client: Any) -> None:
        self._r = redis_client

    @staticmethod
    def _safe_model(model: str) -> str:
        return (model or "unknown").replace(":", "_").replace(" ", "_") or "unknown"

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Normalize query before hashing — strip + casefold-equivalent.

        ``"Hello"`` and ``"hello "`` MUST collide on the same cache key —
        otherwise identical user queries with whitespace/case noise miss
        the cache and re-pay the embedding cost.
        """
        return (query or "").strip().lower()

    def _key(self, query: str, *, model: str) -> str:
        normalized = self._normalize_query(query)
        h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_HASH_HEX_PREFIX]
        return f"ragbot:embed:{self._safe_model(model)}:{h}"

    async def get(self, query: str, *, model: str) -> list[float] | None:
        """Return cached embedding vector or ``None`` on miss / Redis error."""
        if self._r is None or not query:
            return None
        key = self._key(query, model=model)
        try:
            raw = await self._r.get(key)
        except (RedisError, OSError, asyncio.TimeoutError):
            logger.debug("embed_cache_get_failed", key=key, exc_info=True)
            return None
        if not raw:
            return None
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            logger.debug("embed_cache_corrupt_payload", key=key, exc_info=True)
            return None
        if not isinstance(payload, list):
            return None
        return payload

    async def set(
        self,
        query: str,
        embedding: list[float],
        *,
        model: str,
        ttl_s: int,
    ) -> None:
        """Persist embedding under TTL; swallow Redis errors."""
        if self._r is None or not query or not embedding:
            return
        if ttl_s <= 0:
            return
        key = self._key(query, model=model)
        try:
            await self._r.setex(key, int(ttl_s), json.dumps(embedding))
        except (RedisError, OSError, asyncio.TimeoutError, TypeError):
            logger.debug("embed_cache_set_failed", key=key, exc_info=True)


__all__ = ["EmbedCache"]
