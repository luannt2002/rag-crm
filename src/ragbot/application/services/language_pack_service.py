"""Language pack service — Redis-cached resolver in front of the DB repo.

Implements ``LanguagePackPort``. Caching strategy:
- Per-key cache (``ragbot:lpack:<lang>:<prompt_key>``) for ``get(...)`` —
  sized for fine-grained orchestration nodes that touch only one prompt.
- Whole-pack cache (``ragbot:lpack:pack:<lang>``) for ``get_pack(...)`` —
  one Redis round-trip when an orchestrator needs several prompts.

Both TTLs use ``DEFAULT_SERVICE_CACHE_TTL_S`` so admin updates propagate
within ≤ 5 minutes; force-bust by ``DEL ragbot:lpack:*`` after writes.

Fallback chain (per CLAUDE.md "domain-neutral, never crash"):
1. Cache hit on requested ``(language, prompt_key)``.
2. DB row for requested ``(language, prompt_key)``.
3. DB row for ``(DEFAULT_LANGUAGE, prompt_key)``.
4. ``ragbot.shared.i18n`` in-memory fallback (boot-time DB outage guard).
5. Empty string — orchestration code MUST treat ``""`` as "skip prompt".

Bot owners adding a new language ship a 7-row INSERT against
``language_packs`` (one per ``LANGUAGE_PACK_PROMPT_KEYS``). The next
``get`` after cache TTL picks them up — zero code change, single source
of truth, mindset-clean.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from redis.exceptions import RedisError

from ragbot.application.ports.language_pack_port import LanguagePackPort
from ragbot.application.ports.language_pack_repository_port import (
    LanguagePackRepositoryPort,
)
from ragbot.shared.constants import (
    DEFAULT_LANGUAGE,
    DEFAULT_SERVICE_CACHE_TTL_S,
    LANGUAGE_PACK_CACHE_PREFIX,
    LANGUAGE_PACK_PROMPT_KEYS,
)

logger = structlog.get_logger(__name__)


def _key_single(language: str, prompt_key: str) -> str:
    """Redis key for a single prompt entry."""
    return f"{LANGUAGE_PACK_CACHE_PREFIX}{language}:{prompt_key}"


def _key_pack(language: str) -> str:
    """Redis key for the whole-language hash entry (joined with newlines)."""
    return f"{LANGUAGE_PACK_CACHE_PREFIX}pack:{language}"


# Field separator inside the whole-pack cache value. ``\x1f`` (Unit
# Separator) cannot appear inside prompt text by construction (the seed
# migration uses plain UTF-8 prose), so it is safe as a delimiter and
# avoids JSON serialization overhead on the hot read path.
_KV_SEP = "\x1f"
_REC_SEP = "\x1e"


def _encode_pack(pack: dict[str, str]) -> bytes:
    """Encode ``{key: content}`` as ``key␟content␞key␟content...``."""
    parts = [f"{k}{_KV_SEP}{v}" for k, v in pack.items()]
    return _REC_SEP.join(parts).encode("utf-8")


def _decode_pack(raw: bytes) -> dict[str, str]:
    """Inverse of :func:`_encode_pack`. Returns ``{}`` on malformed input."""
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return {}
    out: dict[str, str] = {}
    for record in text.split(_REC_SEP):
        if _KV_SEP not in record:
            continue
        k, v = record.split(_KV_SEP, 1)
        out[k] = v
    return out


class LanguagePackService(LanguagePackPort):
    """Cache-first resolver for platform-internal prompts.

    Redis is best-effort: any cache failure logs and falls through to
    the repository, so a Redis outage degrades latency but never the
    bot's ability to answer.
    """

    def __init__(
        self,
        repo: LanguagePackRepositoryPort,
        redis_client: Any,
        *,
        default_language: str = DEFAULT_LANGUAGE,
        cache_ttl_s: int = DEFAULT_SERVICE_CACHE_TTL_S,
    ) -> None:
        """Initialise with repo + redis client.

        @param repo: ``LanguagePackRepositoryPort`` for DB reads.
        @param redis_client: async redis client (supports ``get``/``set``).
        @param default_language: fallback language when a row is missing.
        @param cache_ttl_s: TTL for per-key and whole-pack cache entries.
        """
        self._repo = repo
        self._redis = redis_client
        self._default_language = default_language
        self._cache_ttl_s = cache_ttl_s

    # ------------------------------------------------------------------
    # Single prompt
    # ------------------------------------------------------------------
    async def get(self, language: str, prompt_key: str) -> str:
        """Return prompt text or ``""`` after exhausting the fallback chain."""
        # 1. Redis per-key cache
        cached = await self._cache_get(_key_single(language, prompt_key))
        if cached is not None:
            return cached

        # 2. DB row for requested language
        content = await self._repo.get_pack(language, prompt_key)
        if content is None and language != self._default_language:
            # 3. DB row for default language
            content = await self._repo.get_pack(self._default_language, prompt_key)

        if content is None:
            # 4. In-memory legacy fallback (DB unseeded / outage at boot).
            content = self._inmemory_fallback(language, prompt_key)

        # 5. Always cache the resolved value (even ``""``) so we don't
        #    hammer the DB for a prompt key that the deployment chooses
        #    to leave blank (e.g. ``greeting_answer``).
        await self._cache_set(_key_single(language, prompt_key), content)
        return content

    async def get_pack(self, language: str) -> dict[str, str]:
        """Return the whole prompt-key map for ``language`` (cached)."""
        # 1. Whole-pack cache
        cached_blob = await self._raw_cache_get(_key_pack(language))
        if cached_blob is not None:
            decoded = _decode_pack(cached_blob)
            if decoded:
                return decoded

        # 2. DB read
        rows = await self._repo.list_pack(language)
        if language != self._default_language:
            # Merge default rows so partially translated languages still
            # provide every canonical prompt key.
            default_rows = await self._repo.list_pack(self._default_language)
            merged = {**default_rows, **rows}  # language-specific wins
        else:
            merged = dict(rows)

        # 3. Fill any still-missing canonical key from in-memory legacy
        #    fallback so callers always get a complete pack.
        for key in LANGUAGE_PACK_PROMPT_KEYS:
            if key not in merged or merged[key] is None:
                merged[key] = self._inmemory_fallback(language, key)

        await self._raw_cache_set(_key_pack(language), _encode_pack(merged))
        return merged

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _inmemory_fallback(language: str, prompt_key: str) -> str:
        """Return the in-memory ``i18n.py`` text for ``(language, key)`` or ``""``.

        Imported lazily to avoid a circular import at module load — the
        i18n module also imports from constants.
        """
        # Local import: small dataclass module; no perf concern.
        from ragbot.shared.i18n import get_pack as _inmem_get_pack

        try:
            pack = _inmem_get_pack(language)
        except (KeyError, TypeError, AttributeError):
            # Defensive: legacy module is a pure dict lookup, but a non-str
            # ``language`` or future hot-swap must never break the caller.
            return ""
        # ``LanguagePack`` field naming is mixed: most prompt keys map to
        # ``prompt_<key>`` (e.g. ``generator`` → ``prompt_generator``) while a
        # few are bare (``greeting_answer``, ``refuse_message``,
        # ``sysprompt_default_rules``). Resolve against the real dataclass:
        # prefer the bare field name, else the ``prompt_<key>`` convention.
        # A blind ``prompt_<key>`` lookup read a non-existent attribute and
        # swallowed the configured refuse text to ``""``.
        if hasattr(pack, prompt_key):
            value = getattr(pack, prompt_key)
        else:
            value = getattr(pack, f"prompt_{prompt_key}", "")
        return str(value or "")

    async def _cache_get(self, key: str) -> str | None:
        raw = await self._raw_cache_get(key)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return None

    async def _raw_cache_get(self, key: str) -> bytes | None:
        if self._redis is None:
            return None
        try:
            return await self._redis.get(key)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.warning("language_pack_cache_get_failed", key=key, err=str(exc))
            return None

    async def _cache_set(self, key: str, value: str) -> None:
        await self._raw_cache_set(key, value.encode("utf-8"))

    async def _raw_cache_set(self, key: str, value: bytes) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, value, ex=self._cache_ttl_s)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.warning("language_pack_cache_set_failed", key=key, err=str(exc))


__all__ = ["LanguagePackService"]
