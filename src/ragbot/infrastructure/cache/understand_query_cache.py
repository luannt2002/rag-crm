"""Redis cache for ``understand_query`` LLM outputs.

The understand_query node runs an LLM call per turn to extract intent +
condensed-query rewrite. Repeat queries inside a user session pay the
full LLM round-trip even though the input is identical. This cache memoises
the JSON payload for ``DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S`` seconds so the
second occurrence within the TTL window short-circuits the LLM call.

Key layout
----------
``ragbot:uq:v{prompt_version}:{record_bot_id}:{sha256(query[:300])[:16]}``

* ``prompt_version`` — ``PROMPT_VERSION_UQ`` constant in
  ``shared.constants``. Bump on any change to the understand-query prompt
  template in ``i18n.py`` so prior cached outputs are namespaced out
  without a manual Redis flush.
* ``record_bot_id`` — bot scope. Two bots with identical input queries
  may classify intent differently (different language pack, different
  guardrail rules), so isolation is required. ``record_bot_id`` alone
  suffices because it is the unique internal PK (see CLAUDE.md 4-key
  resolve flow — internal queries use ``record_bot_id`` only).
* ``sha256(query[:300])[:16]`` — first 300 chars bound key length; 16-hex
  prefix keeps collisions astronomically improbable for realistic Q/s.

Failure mode
------------
All Redis errors degrade silent (CLAUDE.md "graceful degradation" rule):
``get`` returns ``None`` (treated as cache miss), ``set`` swallows. The
caller then issues the underlying LLM call and proceeds normally — the
auxiliary cache MUST NOT break the chat path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

# Cap key derivation input. A 100k-char query has the same first 300 chars
# as its abbreviation; the trade-off accepts that pathological adversarial
# inputs (identical first 300 chars, different bodies) collide — domain
# queries don't behave that way in practice.
_QUERY_KEY_PREFIX_CHARS = 300
_HASH_HEX_PREFIX = 16


class UnderstandQueryCache:
    """Redis-backed memo of understand_query LLM output (intent + condensed query)."""

    def __init__(self, redis_client: Any, *, prompt_version: int) -> None:
        self._r = redis_client
        self._pv = int(prompt_version)

    def _key(self, record_bot_id: str, query: str) -> str:
        h = hashlib.sha256(
            query[:_QUERY_KEY_PREFIX_CHARS].encode("utf-8"),
        ).hexdigest()[:_HASH_HEX_PREFIX]
        return f"ragbot:uq:v{self._pv}:{record_bot_id}:{h}"

    async def get(self, record_bot_id: str, query: str) -> dict[str, Any] | None:
        """Return cached payload or ``None`` on miss / malformed / Redis error."""
        if self._r is None or not record_bot_id or not query:
            return None
        key = self._key(record_bot_id, query)
        try:
            raw = await self._r.get(key)
        except (RedisError, OSError, asyncio.TimeoutError):
            logger.debug("uq_cache_get_failed", key=key, exc_info=True)
            return None
        if not raw:
            return None
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            logger.debug("uq_cache_corrupt_payload", key=key, exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    async def set(
        self,
        record_bot_id: str,
        query: str,
        value: dict[str, Any],
        *,
        ttl_s: int,
    ) -> None:
        """Persist payload under TTL; swallow Redis errors (auxiliary cache)."""
        if self._r is None or not record_bot_id or not query or not isinstance(value, dict):
            return
        if ttl_s <= 0:
            return
        key = self._key(record_bot_id, query)
        try:
            await self._r.setex(key, int(ttl_s), json.dumps(value))
        except (RedisError, OSError, asyncio.TimeoutError, TypeError):
            logger.debug("uq_cache_set_failed", key=key, exc_info=True)


__all__ = ["UnderstandQueryCache"]
