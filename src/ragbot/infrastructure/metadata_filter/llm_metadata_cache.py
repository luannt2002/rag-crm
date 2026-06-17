"""LLMMetadataCache — Redis-backed cache for Generic LLM Metadata Extractor.

Pattern: query_hash (SHA-256) → cached extracted metadata (JSON).
TTL configurable via shared/constants.DEFAULT_METADATA_CACHE_TTL_S (1h default).

Sacred-rule alignment:
- Zero-hardcode: TTL từ constants (pure technical)
- Graceful degradation: redis_client None → no cache, no crash
- Domain-neutral: cache key derived from query content only
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


_CACHE_KEY_PREFIX = "ragbot:metadata_extract:v1"


class LLMMetadataCache:
    """Redis cache wrapper for LLM-extracted metadata.

    Args:
        redis_client: Async Redis client (None → cache disabled, all
            ``get`` returns None and ``set`` is a no-op).
        ttl_seconds: Cache TTL, from shared/constants.DEFAULT_METADATA_CACHE_TTL_S.
    """

    def __init__(self, *, redis_client: Any | None, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl = max(1, int(ttl_seconds))

    @staticmethod
    def _hash_query(query: str) -> str:
        """SHA-256 hash for cache key (collision-resistant)."""
        return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:32]

    def _key(self, query: str, locale: str) -> str:
        h = self._hash_query(query)
        return f"{_CACHE_KEY_PREFIX}:{locale}:{h}"

    async def get(self, query: str, locale: str = "vi") -> dict[str, Any] | None:
        """Lookup cached metadata; return None on miss/error."""
        if self._redis is None or not query:
            return None
        try:
            raw = await self._redis.get(self._key(query, locale))
            if raw is None:
                return None
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "metadata_cache_decode_failed",
                err=str(exc)[:120],
            )
            return None
        except Exception as exc:  # noqa: BLE001 — cache miss is non-fatal
            logger.debug(
                "metadata_cache_get_failed",
                error_type=type(exc).__name__,
                err=str(exc)[:120],
            )
            return None

    async def set(
        self,
        query: str,
        metadata: dict[str, Any],
        locale: str = "vi",
    ) -> None:
        """Store metadata with TTL; silently skip on error."""
        if self._redis is None or not query or not metadata:
            return
        try:
            payload = json.dumps(metadata, ensure_ascii=False)
            await self._redis.set(self._key(query, locale), payload, ex=self._ttl)
        except Exception as exc:  # noqa: BLE001 — cache failure non-fatal
            logger.debug(
                "metadata_cache_set_failed",
                error_type=type(exc).__name__,
                err=str(exc)[:120],
            )


__all__ = ["LLMMetadataCache"]
