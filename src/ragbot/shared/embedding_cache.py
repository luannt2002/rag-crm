"""Redis cache for embedding vectors.

Key includes provider + model + dimension so neither a model swap nor a
provider swap can return stale vectors:
``ragbot:emb:{provider}:{model}:{dim}:{sha256(text)[:16]}``. Two providers
exposing the same wire model id + dim (e.g. a self-hosted clone vs a hosted
API) therefore land in distinct buckets. Value: JSON float list. TTL: 30d.
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
# Explicit sentinel for an unsupplied provider/model. Kept observable IN the
# key (rather than silently dropping the segment) so a missing identity shows
# up in Redis and can be alerted on — see the warning in ``_cache_key``.
_MISSING = "unknown"


def _sanitize_segment(value: str) -> str:
    """Strip Redis key separators so a single segment can't span colons."""
    return value.replace(":", "_").replace(" ", "_")


def _cache_key(text: str, model: str, dim: int, *, provider: str = _MISSING) -> str:
    """Build Redis key from provider + model + dim + text hash.

    Including provider + model + dim in the key prevents a model OR provider
    swap from poisoning the cache (a different provider exposing the same wire
    model id + dim returned for the new provider's requests). ``provider`` is
    keyword-only with an explicit ``_MISSING`` default so existing callers stay
    backward-compatible.

    Missing model is made EXPLICIT, never silently bucketed: the ``_MISSING``
    sentinel stays visible in the key AND a warning is emitted so the gap is
    observable instead of swallowed.
    """
    safe_model = _sanitize_segment(model) or _MISSING
    if safe_model == _MISSING:
        logger.warning("embedding_cache_model_missing", provider=provider, dim=int(dim))
    safe_provider = _sanitize_segment(provider) or _MISSING
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"{_CACHE_PREFIX}{safe_provider}:{safe_model}:{int(dim)}:{text_hash}"


async def get_cached_embedding(
    redis_client,  # noqa: ANN001
    text: str,
    *,
    provider: str = _MISSING,
    model: str = _MISSING,
    dim: int = 0,
) -> list[float] | None:
    """Lay embedding tu Redis cache, scoped by provider + model + dim."""
    if redis_client is None:
        return None
    key = _cache_key(text, model, dim, provider=provider)
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
    provider: str = _MISSING,
    model: str = _MISSING,
    dim: int = 0,
) -> None:
    """Luu embedding vao Redis cache, scoped by provider + model + dim."""
    if redis_client is None or not embedding:
        return
    key = _cache_key(text, model, dim or len(embedding), provider=provider)
    try:
        await redis_client.set(key, json.dumps(embedding), ex=_CACHE_TTL)
    except (RedisError, OSError, asyncio.TimeoutError, TypeError):
        logger.debug("embedding_cache_set_failed", key=key, exc_info=True)


__all__ = ["get_cached_embedding", "set_cached_embedding"]
