"""Per-bot reranker resolver — resolves bot_model_bindings + ai_providers.

Cache layer: Redis ``ragbot:rerank:{record_bot_id}`` with TTL
``DEFAULT_RERANK_CONFIG_TTL_S`` (60 s default).

Resolution order (per-bot override → platform default):
  1. ``bot_model_bindings`` (purpose='rerank') — bot-specific override.
  2. ``system_config`` (``reranker_enabled`` + ``reranker_model`` +
     ``reranker_provider``) + matching ``ai_models`` + ``ai_providers``
     row — platform default applied to every bot that hasn't opted out.

Fail-soft contract:
  - No binding AND platform default disabled / model row missing →
    NullReranker.
  - Redis failure → fall back to DB (log warning, don't crash).
  - Empty / missing API key → NullReranker (log warning).
  - Provider build failure → NullReranker (log warning).

REUSES existing tables: ai_providers + ai_models + bot_model_bindings +
system_config. NO new schema required. To onboard a new provider:
  1. Insert row into ai_providers.
  2. Insert model row into ai_models (kind='reranker').
  3. (optional) Set system_config.reranker_model so every bot inherits.
  4. (optional) Insert bot_model_bindings row (purpose='rerank') for a
     bot-specific override.
  5. Register provider key in infrastructure.reranker.registry if not
     already there.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.ports.reranker_port import RerankerPort
from ragbot.infrastructure.reranker.null_reranker import NullReranker
from ragbot.infrastructure.reranker.registry import build_reranker
from ragbot.shared.api_key_pool import ApiKeyPoolFactory
from ragbot.shared.constants import DEFAULT_RERANK_CONFIG_TTL_S

logger = structlog.get_logger(__name__)

REDIS_KEY_PREFIX = "ragbot:rerank:"

# SQL: resolve active reranker binding for a bot.
# Joins bot_model_bindings → ai_models (kind='reranker') → ai_providers.
# Only active bindings on enabled models and providers are considered.
_RESOLVE_SQL = text("""
    SELECT
        m.name         AS model_name,
        p.code         AS provider_code,
        p.api_key_ref  AS api_key_ref,
        p.api_key_encrypted AS api_key_encrypted,
        p.base_url     AS base_url,
        m.metadata_json AS model_meta
    FROM bot_model_bindings b
    JOIN ai_models    m ON b.record_model_id    = m.id
    JOIN ai_providers p ON m.record_provider_id = p.id
    WHERE b.record_bot_id = :bid
      AND b.purpose       = 'rerank'
      AND b.active        = true
      AND b.deleted_at    IS NULL
      AND m.enabled       = true
      AND m.deleted_at    IS NULL
      AND p.enabled       = true
      AND p.deleted_at    IS NULL
    ORDER BY b.rank
    LIMIT 1
""")


class RerankerResolver:
    """Resolve the per-bot reranker via DB + Redis cache.

    Conforms to ``RerankerResolverPort`` via structural typing (Protocol).

    Args:
        session_factory: SQLAlchemy async session factory.
        redis_client: redis.asyncio client (any subset implementing get/setex).
        ttl_s: Redis TTL in seconds. Default ``DEFAULT_RERANK_CONFIG_TTL_S``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: Any,
        ttl_s: int = DEFAULT_RERANK_CONFIG_TTL_S,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        ledger: Any = None,
    ) -> None:
        self._sf = session_factory
        self._redis = redis_client
        self._ttl = ttl_s
        # Log-center: per-bot rerankers emit their token usage to this ledger
        # (action="rerank"). None → adapter no-ops the emit.
        self._ledger = ledger
        # Multi-key pool factory — passed to every per-bot reranker so the
        # provider adapter resolves an N-key round-robin pool (BPM failover)
        # instead of a single env key. Without it a 429 cannot rotate and
        # degrades straight to RRF. ``None`` keeps the single-key legacy path.
        self._key_pool_factory = key_pool_factory

    async def resolve_for_bot(self, record_bot_id: UUID) -> RerankerPort:
        """Return the reranker for this bot. Falls back to NullReranker on any failure."""
        bot_id_str = str(record_bot_id)
        cache_key = f"{REDIS_KEY_PREFIX}{bot_id_str}"

        # --- 1. Try Redis cache ---
        config: dict | None = None
        try:
            raw = await self._redis.get(cache_key)
            if raw is not None:
                decoded = raw if isinstance(raw, str) else raw.decode()
                cached = json.loads(decoded)
                # Empty dict = negative cache (no binding found last time)
                logger.debug(
                    "rerank_resolver_cache_hit",
                    record_bot_id=bot_id_str,
                    has_config=bool(cached),
                )
                return self._build_from_config(cached if cached else None)
        except (RedisError, OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            logger.warning(
                "rerank_resolver_cache_read_failed",
                record_bot_id=bot_id_str,
                error=str(exc)[:200],
                exc_info=False,
            )

        # --- 2. DB lookup ---
        try:
            config = await self._lookup_db(record_bot_id)
        except (SQLAlchemyError, OSError, ValueError) as exc:
            logger.warning(
                "rerank_resolver_db_failed",
                record_bot_id=bot_id_str,
                error=str(exc)[:200],
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return NullReranker()

        # --- 3. Write to Redis cache (positive or negative) ---
        try:
            payload = json.dumps(config if config else {})
            await self._redis.setex(cache_key, self._ttl, payload)
        except (RedisError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "rerank_resolver_cache_write_failed",
                record_bot_id=bot_id_str,
                error=str(exc)[:200],
                exc_info=False,
            )

        return self._build_from_config(config)

    async def _lookup_db(self, record_bot_id: UUID) -> dict | None:
        """Resolve reranker config: per-bot binding → platform default → None.

        Per-bot binding wins so a bot can opt out (insert ``active=false``
        row) or use a different vendor than the platform default. When no
        binding exists, fall through to ``system_config.reranker_*`` which
        every bot shares — same model already loaded by the DI container.
        """
        async with self._sf() as session:
            result = await session.execute(_RESOLVE_SQL, {"bid": str(record_bot_id)})
            row = result.mappings().first()
            if row:
                return {
                    "model_name": row["model_name"],
                    "provider_code": row["provider_code"],
                    "api_key_ref": row["api_key_ref"],
                    "api_key_encrypted": row["api_key_encrypted"],
                    "base_url": row["base_url"],
                    "model_meta": (
                        dict(row["model_meta"]) if row["model_meta"] else {}
                    ),
                }

            default = await self._lookup_platform_default(session)
            if default is None:
                logger.debug(
                    "rerank_resolver_no_binding_no_default",
                    record_bot_id=str(record_bot_id),
                )
            else:
                logger.debug(
                    "rerank_resolver_using_platform_default",
                    record_bot_id=str(record_bot_id),
                    provider=default.get("provider_code"),
                    model=default.get("model_name"),
                )
            return default

    async def _lookup_platform_default(
        self, session: AsyncSession,
    ) -> dict | None:
        """Look up reranker config from ``system_config`` + ``ai_*`` tables.

        Returns ``None`` when:
          - ``reranker_enabled`` is not truthy in system_config, or
          - ``reranker_model`` / ``reranker_provider`` not set, or
          - matching ``ai_models`` + ``ai_providers`` row not present /
            disabled (operator wired system_config but forgot the model
            row — fail-soft instead of raising at request time).
        """
        cfg = await session.execute(
            text(
                "SELECT key, value FROM system_config "
                "WHERE key IN ('reranker_enabled', 'reranker_model', 'reranker_provider')",
            ),
        )
        cfg_map: dict[str, str] = {}
        for key, value in cfg.fetchall():
            if value is None:
                continue
            cfg_map[key] = str(value).strip().strip('"')

        enabled_raw = cfg_map.get("reranker_enabled", "").lower()
        if enabled_raw not in ("true", "1", "yes"):
            return None

        model_name = cfg_map.get("reranker_model", "")
        provider_code = cfg_map.get("reranker_provider", "")
        if not model_name or not provider_code:
            return None

        row = await session.execute(
            text(
                """
                SELECT
                    m.name           AS model_name,
                    p.code           AS provider_code,
                    p.api_key_ref    AS api_key_ref,
                    p.api_key_encrypted AS api_key_encrypted,
                    p.base_url       AS base_url,
                    m.metadata_json  AS model_meta
                FROM ai_models m
                JOIN ai_providers p ON m.record_provider_id = p.id
                WHERE m.name = :model_name
                  AND m.kind = 'reranker'
                  AND m.enabled = true
                  AND m.deleted_at IS NULL
                  AND p.code = :provider_code
                  AND p.enabled = true
                  AND p.deleted_at IS NULL
                LIMIT 1
                """,
            ),
            {"model_name": model_name, "provider_code": provider_code},
        )
        record = row.mappings().first()
        if not record:
            # reranker_enabled=true but no (model, provider) row matched — a
            # system_config drift (e.g. provider 'jina' ⊥ model 'zerank-2')
            # silently degraded EVERY binding-less bot to NullReranker. Fail
            # LOUD so the next drift is caught immediately, not via a coverage
            # regression weeks later (silent-fallback ban, v2 bug lessons).
            logger.warning(
                "reranker_platform_default_unresolved_falling_back_to_null",
                reranker_model=model_name,
                reranker_provider=provider_code,
                hint="system_config reranker_model/provider must match an "
                "enabled ai_models.name + ai_providers.code row",
            )
            return None
        return {
            "model_name": record["model_name"],
            "provider_code": record["provider_code"],
            "api_key_ref": record["api_key_ref"],
            "api_key_encrypted": record["api_key_encrypted"],
            "base_url": record["base_url"],
            "model_meta": (
                dict(record["model_meta"]) if record["model_meta"] else {}
            ),
        }

    def _build_from_config(self, config: dict | None) -> RerankerPort:
        """Build a reranker from resolved DB config. Returns NullReranker on any error."""
        if not config or not config.get("provider_code"):
            return NullReranker()

        provider = config["provider_code"]

        # Resolve API key: env var (primary) or encrypted column (deferred).
        api_key: str = ""
        api_key_ref = config.get("api_key_ref")
        if api_key_ref:
            api_key = os.getenv(api_key_ref, "")
        if not api_key and config.get("api_key_encrypted"):
            # AES decrypt path not implemented; fall back to NullReranker.
            logger.warning(
                "rerank_resolver_encrypted_key_not_implemented",
                provider=provider,
            )
            return NullReranker()
        if not api_key:
            logger.warning(
                "rerank_resolver_api_key_empty",
                provider=provider,
                env_var=api_key_ref,
            )
            return NullReranker()

        try:
            reranker = build_reranker(
                provider=provider,
                api_key=api_key,
                model=config.get("model_name"),
                # Hand the multi-key pool factory to the adapter so a 429 can
                # round-robin across keys (BPM failover) rather than degrade
                # to RRF. ``api_key`` stays the single-key legacy fallback.
                key_pool_factory=self._key_pool_factory,
                ledger=self._ledger,
            )
            logger.debug(
                "rerank_resolver_built",
                provider=provider,
                model=config.get("model_name"),
            )
            return reranker
        except (ValueError, ImportError, KeyError, TypeError) as exc:
            logger.warning(
                "rerank_resolver_build_failed",
                provider=provider,
                error=str(exc)[:200],
                exc_info=True,
            )
            return NullReranker()


__all__ = ["REDIS_KEY_PREFIX", "RerankerResolver"]
