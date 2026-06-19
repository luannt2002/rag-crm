"""ModelResolverService — cache concern (L1 LRU + L2 Redis). Tách từ __init__.py 2026-06-19."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from ragbot.application.dto.ai_specs import (
    BindingPurpose,
    EmbeddingSpec,
    LLMSpec,
    PromptTemplate,
    RerankerSpec,
)
from ragbot.application.dto.model_runtime import (
    Capabilities,
    GenerationParams,
    ModelRuntimeConfig,
    Pricing,
    ProviderRuntime,
    compute_version_hash,
)
from ragbot.application.ports.ai_config_port import (
    AIConfigRepositoryPort,
    BindingRow,
    ModelRow,
    PromptTemplateRow,
    ProviderRow,
)
from ragbot.application.ports.cache_port import CachePort
from ragbot.application.ports.secrets_port import SecretsPort
from ragbot.shared.constants import (
    CACHE_KEY_MODEL_RESOLVER,
    DEFAULT_CASCADE_T_HIGH,
    DEFAULT_CASCADE_T_LOW,
    DEFAULT_CHEAP_INTENT_PURPOSES,
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_TASK_PASSAGE,
    DEFAULT_LLM_PURPOSE_ENRICHMENT,
    DEFAULT_LLM_PURPOSE_PRIMARY,
    DEFAULT_MODEL_RESOLVER_CACHE_TTL,
    DEFAULT_MODEL_RESOLVER_L1_MAX_SIZE,
    DEFAULT_MODEL_RESOLVER_L1_TTL_S,
    DEFAULT_MODEL_RESOLVER_L2_TTL_S,
    DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS,
    DEFAULT_PROVIDER_MAX_CONCURRENT,
    DEFAULT_PROVIDER_MAX_RETRIES,
    DEFAULT_PROVIDER_TIMEOUT_MS,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_RERANKER_EMBEDDING_DIM,
)
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.types import (
    BotId,
    ConversationId,
    LLMIntent,
    TenantId,
)

if TYPE_CHECKING:
    from ragbot.shared.clock import Clock

logger = structlog.get_logger(__name__)
# Pure helpers extracted to _helpers (strangler split).
from ragbot.application.services.model_resolver._helpers import *  # noqa: E402,F401,F403

# Intents whose LLM call is ingest-side enrichment (extractive, high volume) —
# routed to the cheaper ``enrichment`` purpose instead of the answer model.
_ENRICHMENT_INTENTS: frozenset[str] = frozenset({"contextualization", "enrichment"})


class CacheMixin:
    """ModelResolverService — cache concern (L1 LRU + L2 Redis). Tách từ __init__.py 2026-06-19."""

    async def invalidate_all(self) -> None:
        """Flush entire in-process cache (Redis relies on TTL)."""
        self._mem.clear()
        self._l1.clear()
        logger.info("model_resolver_invalidated_all")

    async def invalidate(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None = None,
    ) -> None:
        """Clear caches (in-process + Redis prefix)."""
        prefix = f"{CACHE_KEY_MODEL_RESOLVER}:{record_tenant_id}"
        if record_bot_id is not None:
            prefix = f"{prefix}:{record_bot_id}:"
            self._mem = {k: v for k, v in self._mem.items() if not k.startswith(prefix)}
        else:
            self._mem = {k: v for k, v in self._mem.items() if not k.startswith(prefix + ":")}
        logger.info("model_resolver_invalidated", tenant_id=str(record_tenant_id), bot_id=str(record_bot_id))

    async def bootstrap_cache(self) -> int:
        """Prime L1 cache from DB: every enabled binding → ModelRuntimeConfig.

        Returns number of entries loaded.
        """
        providers = await self._repo.list_providers(enabled_only=True)
        providers_by_id = {str(p.id): p for p in providers}
        models = await self._repo.list_models(enabled_only=True)
        models_by_id = {str(m.id): m for m in models}

        scanned: list[BindingRow] = []
        scan_fn = getattr(self._repo, "scan_bindings", None)
        if callable(scan_fn):
            try:
                scanned = await scan_fn(enabled_only=True)
            except Exception:  # noqa: BLE001
                logger.warning("scan_bindings_failed")

        count = 0
        for b in scanned:
            m = models_by_id.get(str(b.model_id))
            if m is None:
                continue
            p = providers_by_id.get(str(m.provider_id))
            if p is None:
                continue
            try:
                cfg = await self._build_runtime(
                    binding=b, model=m, provider=p, purpose=b.purpose,
                )
            except Exception:  # noqa: BLE001
                logger.warning("bootstrap_build_failed", binding_id=str(b.id))
                continue
            # Bindings carry record_tenant_id / record_bot_id (internal-key naming).
            key = self._runtime_cache_key(b.record_tenant_id, b.record_bot_id, b.purpose)
            self._l1_put(key, cfg)
            count += 1

        self._last_bootstrap_at = self._clock.now()
        logger.info("model_resolver_bootstrap_complete", entries=count)
        return count

    async def cache_status(self) -> dict[str, object]:
        """Return L1 runtime cache telemetry + bindings cache snapshot."""
        now = self._clock.monotonic()
        keys = list(self._l1.keys())[:10]
        version_hashes = {k: v[0].version_hash for k, v in list(self._l1.items())[:10]}
        return {
            "l1_size": len(self._l1),
            "l1_keys": keys,
            "last_bootstrap_at": (
                self._last_bootstrap_at.isoformat()
                if self._last_bootstrap_at
                else None
            ),
            "version_hashes": version_hashes,
            "entries": len(self._mem),
            "ttl_s": self._ttl,
            "keys": [
                {
                    "key": k,
                    "age_s": max(0.0, now - v.cached_at),
                    "bindings": len(v.bindings),
                }
                for k, v in self._mem.items()
            ],
        }

    @staticmethod
    def _runtime_cache_key(
        record_tenant_id: UUID | None,
        record_bot_id: UUID | None,
        purpose: str,
    ) -> str:
        """Build the L1/Redis cache key for runtime model lookup.

        Both ids are internal UUIDs per naming convention. Typed as
        optional UUID because the bootstrap path may encounter
        bindings without a tenant (default bindings apply globally).
        """
        return f"model_runtime:{record_tenant_id}:{record_bot_id}:{purpose}"

    def _l1_put(self, key: str, cfg: ModelRuntimeConfig) -> None:
        self._l1[key] = (cfg, self._clock.monotonic())
        self._l1.move_to_end(key)
        while len(self._l1) > self._l1_max:
            self._l1.popitem(last=False)

    def _l1_get(self, key: str) -> ModelRuntimeConfig | None:
        hit = self._l1.get(key)
        if hit is None:
            return None
        cfg, ts = hit
        if (self._clock.monotonic() - ts) > self._l1_ttl:
            self._l1.pop(key, None)
            return None
        self._l1.move_to_end(key)
        return cfg

    async def _get_cached(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        purpose: str,
    ) -> _CachedBindings:
        cache_key = f"{CACHE_KEY_MODEL_RESOLVER}:{record_tenant_id!s}:{record_bot_id!s}:{purpose}"

        hit = self._mem.get(cache_key)
        if hit and (self._clock.monotonic() - hit.cached_at) < self._ttl:
            return hit

        raw = await self._cache.get(cache_key)
        if raw is not None:
            try:
                data = json.loads(raw)
                rebuilt = self._deserialize_cached(data)
                self._mem[cache_key] = rebuilt
                return rebuilt
            except (json.JSONDecodeError, KeyError):  # pragma: no cover
                logger.warning("model_resolver_cache_decode_failed", key=cache_key)

        bindings = await self._repo.list_bindings(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            purpose=purpose,
        )
        models_by_id: dict[str, ModelRow] = {}
        providers_by_id: dict[str, ProviderRow] = {}
        # Single batch SQL round-trip — replaces previous fan-out
        # (asyncio.gather of N concurrent ``get_model`` calls). Cold-cache
        # P99 dropped from ~400ms to ~50-100ms because 20-30 sequential
        # ``SELECT ai_models WHERE id = ?`` collapse into one
        # ``SELECT ... WHERE id IN (...)``. Per Agent F P0 #3 + Agent P.
        if bindings:
            unique_model_ids = list({b.model_id for b in bindings})
            models_by_id = await self._repo.get_models_by_ids(unique_model_ids)
            unique_provider_ids = list({
                m.provider_id for m in models_by_id.values()
            })
            if unique_provider_ids:
                providers_by_id = await self._repo.get_providers_by_ids(
                    unique_provider_ids,
                )
            # Warn for bindings whose model row is absent (orphan FK) so
            # operators see the gap — previous fan-out logged this case
            # per-binding; we preserve the structured event shape.
            for b in bindings:
                if str(b.model_id) not in models_by_id:
                    logger.warning(
                        "model_missing_for_binding",
                        binding_id=str(b.id),
                        model_id=str(b.model_id),
                    )

        cached = _CachedBindings(
            bindings=tuple(bindings),
            models_by_id=models_by_id,
            providers_by_id=providers_by_id,
            cached_at=self._clock.monotonic(),
        )

        self._mem[cache_key] = cached
        try:
            await self._cache.set(
                cache_key,
                json.dumps(self._serialize_cached(cached)).encode("utf-8"),
                ttl_s=self._ttl,
            )
        except Exception:  # noqa: BLE001
            logger.warning("model_resolver_cache_set_failed", key=cache_key)

        return cached

    @staticmethod
    def _serialize_cached(c: _CachedBindings) -> dict[str, object]:
        return {
            "bindings": [_dataclass_dict(b) for b in c.bindings],
            "models_by_id": {k: _dataclass_dict(v) for k, v in c.models_by_id.items()},
            "providers_by_id": {k: _dataclass_dict(v) for k, v in c.providers_by_id.items()},
            "cached_at": time.time(),
        }

    def _deserialize_cached(self, data: dict[str, object]) -> _CachedBindings:
        bindings = tuple(
            _dict_to_dataclass(BindingRow, raw)
            for raw in data.get("bindings", [])  # type: ignore[arg-type]
        )
        models_by_id = {
            k: _dict_to_dataclass(ModelRow, v)
            for k, v in (data.get("models_by_id") or {}).items()  # type: ignore[union-attr]
        }
        providers_by_id = {
            k: _dict_to_dataclass(ProviderRow, v)
            for k, v in (data.get("providers_by_id") or {}).items()  # type: ignore[union-attr]
        }
        return _CachedBindings(
            bindings=bindings,
            models_by_id=models_by_id,
            providers_by_id=providers_by_id,
            cached_at=self._clock.monotonic(),
        )
