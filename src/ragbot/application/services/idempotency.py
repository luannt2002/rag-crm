"""IdempotencyService — Redis SETNX-based dedup.

Ref: PLAN_07 §idempotency.py / RAGBOT_MASTER §14.4.
"""

from __future__ import annotations

from ragbot.application.ports.cache_port import CachePort
from ragbot.shared.constants import CACHE_KEY_IDEMPOTENCY, DEFAULT_IDEMPOTENCY_TTL
from ragbot.shared.types import IdempotencyKey


class IdempotencyService:
    def __init__(self, cache: CachePort, *, ttl_s: int = DEFAULT_IDEMPOTENCY_TTL) -> None:
        self._cache = cache
        self._ttl = ttl_s

    @staticmethod
    def _build_key(key: IdempotencyKey) -> str:
        return f"{CACHE_KEY_IDEMPOTENCY}:{key}"

    async def is_duplicate(self, key: IdempotencyKey) -> bool:
        return await self._cache.exists(self._build_key(key))

    async def register(self, key: IdempotencyKey, *, result_ref: str | None = None) -> None:
        payload = (result_ref or "").encode("utf-8")
        await self._cache.set(self._build_key(key), payload, ttl_s=self._ttl)

    async def get_prior_result_ref(self, key: IdempotencyKey) -> str | None:
        raw = await self._cache.get(self._build_key(key))
        if raw is None:
            return None
        return raw.decode("utf-8") or None


__all__ = ["IdempotencyService"]
