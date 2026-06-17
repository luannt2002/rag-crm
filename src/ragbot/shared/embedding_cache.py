"""Redis cache for embedding vectors.

Key includes model + dimension so a model swap cannot return stale vectors:
``ragbot:emb:{model}:{dim}:{sha256(text)[:16]}``. Value: JSON float list. TTL: 30d.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

_CACHE_PREFIX = "ragbot:emb:"
_CACHE_TTL = 30 * 24 * 3600  # 30 days


def _cache_key(text: str, model: str, dim: int) -> str:
    """Build Redis key from model + dim + text hash.

    Including model + dim in the key prevents a model swap from poisoning
    the cache (old model's vectors returned for new model's requests).
    """
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    # Sanitize model name for Redis (slashes, colons allowed but keep clean)
    safe_model = model.replace(":", "_").replace(" ", "_") or "unknown"
    return f"{_CACHE_PREFIX}{safe_model}:{int(dim)}:{text_hash}"


async def get_cached_embedding(
    redis_client,  # noqa: ANN001
    text: str,
    *,
    model: str = "unknown",
    dim: int = 0,
) -> list[float] | None:
    """Lay embedding tu Redis cache, scoped by model + dim."""
    if redis_client is None:
        return None
    key = _cache_key(text, model, dim)
    try:
        raw = await redis_client.get(key)
        if raw:
            return json.loads(raw)
    except (RedisError, OSError, asyncio.TimeoutError, ValueError, TypeError):
        logger.debug("embedding_cache_get_failed", key=key, exc_info=True)
    return None


async def set_cached_embedding(
    redis_client,  # noqa: ANN001
    text: str,
    embedding: list[float],
    *,
    model: str = "unknown",
    dim: int = 0,
) -> None:
    """Luu embedding vao Redis cache, scoped by model + dim."""
    if redis_client is None or not embedding:
        return
    key = _cache_key(text, model, dim or len(embedding))
    try:
        await redis_client.set(key, json.dumps(embedding), ex=_CACHE_TTL)
    except (RedisError, OSError, asyncio.TimeoutError, TypeError):
        logger.debug("embedding_cache_set_failed", key=key, exc_info=True)


__all__ = ["get_cached_embedding", "set_cached_embedding"]
