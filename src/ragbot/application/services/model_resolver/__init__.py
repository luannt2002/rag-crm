"""DB-driven AI strategy resolver.

Looks up bindings / prompt templates / model rows and exposes typed specs
(``LLMSpec``, ``RerankerSpec``, ``EmbeddingSpec``, ``PromptTemplate``).
Two-tier cache: in-process LRU + Redis. Invalidated on ``bot.config_updated.v1``.
"""

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


class ModelResolverService:
    """Resolve model specs from DB with caching + A/B variant + cascade."""

    def __init__(
        self,
        repo: AIConfigRepositoryPort,
        cache: CachePort,
        clock: Clock,
        *,
        cache_ttl_s: int = DEFAULT_MODEL_RESOLVER_CACHE_TTL,
        secrets_port: SecretsPort | None = None,
        l1_ttl_s: int = DEFAULT_MODEL_RESOLVER_L1_TTL_S,
        l2_ttl_s: int = DEFAULT_MODEL_RESOLVER_L2_TTL_S,
        l1_max_size: int = DEFAULT_MODEL_RESOLVER_L1_MAX_SIZE,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._clock = clock
        self._ttl = cache_ttl_s
        # Local in-process cache for hot path (per-process)
        self._mem: dict[str, _CachedBindings] = {}
        # Runtime-config: L1 LRU + L2 Redis.
        self._secrets = secrets_port
        self._l1_ttl = l1_ttl_s
        self._l2_ttl = l2_ttl_s
        self._l1_max = l1_max_size
        self._l1: OrderedDict[str, tuple[ModelRuntimeConfig, float]] = OrderedDict()
        self._last_bootstrap_at: datetime | None = None

    # ----- Public API -------------------------------------------------------
    async def resolve_llm(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        intent: LLMIntent,
        iter: int = 0,  # noqa: A002 — match Plan signature
        conversation_id: ConversationId | None = None,
    ) -> LLMSpec:
        """Resolve LLM spec for a bot+intent at a given iteration."""
        purpose = self._intent_to_purpose(intent)
        cached = await self._get_cached(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, purpose=purpose)
        bindings = sorted(cached.bindings, key=lambda b: b.rank)
        if not bindings and purpose != DEFAULT_LLM_PURPOSE_PRIMARY:
            # No per-bot binding for this cost-routing purpose → fall back to
            # the always-present llm_primary. A bot opts into a cheaper model
            # for this purpose by seeding its own binding (custom > shared);
            # without one it transparently reuses the primary answer model.
            purpose = DEFAULT_LLM_PURPOSE_PRIMARY
            cached = await self._get_cached(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, purpose=purpose)
            bindings = sorted(cached.bindings, key=lambda b: b.rank)
        if not bindings:
            raise InvariantViolation(
                f"No LLM binding found for bot {record_bot_id} purpose={purpose}",
            )

        if iter == 0:
            chosen = self._select_primary_with_ab(bindings, conversation_id)
        else:
            fallbacks = [b for b in bindings if b.rank >= 1]
            if iter > len(fallbacks):
                raise InvariantViolation(
                    f"iter={iter} exceeds fallback chain length {len(fallbacks)}",
                )
            chosen = fallbacks[iter - 1]

        return self._binding_to_spec(chosen, cached)

    async def resolve_fallback_chain(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        intent: LLMIntent,
    ) -> list[LLMSpec]:
        purpose = self._intent_to_purpose(intent)
        cached = await self._get_cached(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, purpose=purpose)
        sorted_bs = sorted(cached.bindings, key=lambda b: b.rank)
        return [self._binding_to_spec(b, cached) for b in sorted_bs]

    async def resolve_reranker(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
    ) -> RerankerSpec:
        cached = await self._get_cached(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            purpose=BindingPurpose.RERANK.value,
        )
        if not cached.bindings:
            raise InvariantViolation(f"No reranker binding for bot {record_bot_id}")
        b = cached.bindings[0]
        m = cached.models_by_id[str(b.model_id)]
        p = cached.providers_by_id[str(m.provider_id)]
        return RerankerSpec(
            binding_id=b.id,
            model_name=m.name,
            provider=p.code,
            top_n=b.extra_params.get("top_n", DEFAULT_RERANK_TOP_N),
            batch_size=b.extra_params.get("batch_size", DEFAULT_RERANK_TOP_N * 10),
        )

    async def resolve_multi_purpose(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        purposes: list[str],
    ) -> dict[str, LLMSpec | RerankerSpec | EmbeddingSpec]:
        """Resolve multiple purpose specs in ONE DB round-trip.

        Replaces N sequential ``resolve_llm`` / ``resolve_reranker`` /
        ``resolve_embedding`` calls when the caller (chat-worker boot,
        admin preview, health probe) needs more than one binding. The
        cold path drops from 3 sequential SELECT trips to one
        ``purpose IN (...)`` SELECT + the existing batched model +
        provider hydrate.

        Per-purpose cached path is preserved: a fully-warm L1 hit short-
        circuits before any DB call; only the cold/missing purposes go
        to the consolidated query.

        Backward-compat: existing single-purpose ``resolve_*`` methods
        stay untouched.

        Args:
            record_bot_id: bot UUID to resolve for.
            record_tenant_id: tenant scope (NULL-tenant bindings still
                match per :meth:`list_bindings_multi_purpose`).
            purposes: list of binding purposes (e.g. ``["llm",
                "embedding", "rerank"]``). Empty list → empty dict.

        Returns:
            ``{purpose: spec}`` map. Missing purposes (no binding) are
            OMITTED from the result so callers can detect gaps with
            ``key not in out``. Raises only on infrastructure failure;
            individual missing bindings degrade silently to "absent in
            map" (mirrors single-purpose semantics where caller decides
            whether to raise or fall back).

        Note:
            Spec type depends on purpose:
            - ``"llm"`` / ``"llm_*"`` → :class:`LLMSpec`
            - ``"embedding"`` → :class:`EmbeddingSpec`
            - ``"rerank"`` → :class:`RerankerSpec`
        """
        if not purposes:
            return {}

        # Warm-path short-circuit: try the in-process cache key for each
        # purpose first. Hits skip the DB entirely; misses go to the
        # consolidated query below. Cache TTL match the single-purpose
        # path so behavior stays observable + consistent.
        result: dict[str, LLMSpec | RerankerSpec | EmbeddingSpec] = {}
        cold_purposes: list[str] = []
        for purpose in purposes:
            cache_key = (
                f"{CACHE_KEY_MODEL_RESOLVER}:{record_tenant_id!s}:"
                f"{record_bot_id!s}:{purpose}"
            )
            hit = self._mem.get(cache_key)
            if hit and (self._clock.monotonic() - hit.cached_at) < self._ttl:
                spec = self._first_spec_from_cached(purpose, hit)
                if spec is not None:
                    result[purpose] = spec
                # If cached but no binding → still skip cold query.
                continue
            cold_purposes.append(purpose)

        if not cold_purposes:
            return result

        # Single SQL round-trip — replaces N sequential ``list_bindings``.
        bindings_by_purpose = await self._repo.list_bindings_multi_purpose(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            purposes=cold_purposes,
        )

        # Batch model + provider hydrate across ALL cold purposes — one
        # IN-clause each, not one per purpose.
        all_bindings = [
            b
            for binds in bindings_by_purpose.values()
            for b in binds
        ]
        models_by_id: dict[str, ModelRow] = {}
        providers_by_id: dict[str, ProviderRow] = {}
        if all_bindings:
            unique_model_ids = list({b.model_id for b in all_bindings})
            models_by_id = await self._repo.get_models_by_ids(unique_model_ids)
            unique_provider_ids = list({
                m.provider_id for m in models_by_id.values()
            })
            if unique_provider_ids:
                providers_by_id = await self._repo.get_providers_by_ids(
                    unique_provider_ids,
                )

        now = self._clock.monotonic()
        for purpose in cold_purposes:
            binds = bindings_by_purpose.get(purpose, [])
            cached = _CachedBindings(
                bindings=tuple(binds),
                models_by_id=models_by_id,
                providers_by_id=providers_by_id,
                cached_at=now,
            )
            # Warm the per-purpose L1 cache so the next single-purpose
            # call also hits without a DB trip.
            cache_key = (
                f"{CACHE_KEY_MODEL_RESOLVER}:{record_tenant_id!s}:"
                f"{record_bot_id!s}:{purpose}"
            )
            self._mem[cache_key] = cached
            spec = self._first_spec_from_cached(purpose, cached)
            if spec is not None:
                result[purpose] = spec

        return result

    def _first_spec_from_cached(
        self,
        purpose: str,
        cached: _CachedBindings,
    ) -> LLMSpec | RerankerSpec | EmbeddingSpec | None:
        """Map a cached binding bundle → typed spec, purpose-aware.

        Returns ``None`` when the bundle has no bindings (caller-side
        decides whether absence is an error).
        """
        if not cached.bindings:
            return None
        b = sorted(cached.bindings, key=lambda x: x.rank)[0]
        m = cached.models_by_id.get(str(b.model_id))
        p = cached.providers_by_id.get(str(m.provider_id)) if m else None
        if m is None or p is None:
            # Orphan FK — surface as missing so caller can choose to
            # raise or fall back. Single-purpose path also tolerates
            # this; we keep parity.
            logger.warning(
                "model_resolver_multi_orphan",
                binding_id=str(b.id),
                purpose=purpose,
                model_id=str(b.model_id),
            )
            return None
        if purpose == BindingPurpose.RERANK.value:
            return RerankerSpec(
                binding_id=b.id,
                model_name=m.name,
                provider=p.code,
                top_n=b.extra_params.get("top_n", DEFAULT_RERANK_TOP_N),
                batch_size=b.extra_params.get(
                    "batch_size",
                    DEFAULT_RERANK_TOP_N * 10,
                ),
            )
        if purpose == BindingPurpose.EMBEDDING.value:
            task_default = b.extra_params.get(
                "task_passage",
                DEFAULT_EMBEDDING_TASK_PASSAGE,
            )
            wire_model = format_litellm_model(m.name, p)
            return EmbeddingSpec(
                binding_id=b.id,
                model_name=wire_model,
                provider=p.code,
                dimension=b.extra_params.get(
                    "dimension", DEFAULT_RERANKER_EMBEDDING_DIM,
                ),
                max_batch=b.extra_params.get(
                    "max_batch", DEFAULT_EMBEDDING_MAX_BATCH,
                ),
                model_version=b.extra_params.get("model_version", m.name),
                task=task_default,
            )
        # Default = LLM purpose. We reuse the existing binding→spec
        # mapper to keep variant / param coercion identical.
        return self._binding_to_spec(b, cached)

    async def resolve_embedding(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
    ) -> EmbeddingSpec:
        cached = await self._get_cached(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            purpose=BindingPurpose.EMBEDDING.value,
        )
        if not cached.bindings:
            raise InvariantViolation(f"No embedding binding for bot {record_bot_id}")
        b = cached.bindings[0]
        m = cached.models_by_id[str(b.model_id)]
        p = cached.providers_by_id[str(m.provider_id)]
        # Default = passage adapter; query path overrides via model_copy.
        task_default = b.extra_params.get(
            "task_passage",
            DEFAULT_EMBEDDING_TASK_PASSAGE,
        )
        # LiteLLM wire: {provider_code}/{model_name} or bare per
        # ``ai_providers.requires_prefix`` (alembic 010e). Already-prefixed
        # names pass through. See :func:`format_litellm_model`.
        wire_model = format_litellm_model(m.name, p)
        return EmbeddingSpec(
            binding_id=b.id,
            model_name=wire_model,
            provider=p.code,
            dimension=b.extra_params.get("dimension", DEFAULT_RERANKER_EMBEDDING_DIM),
            max_batch=b.extra_params.get("max_batch", DEFAULT_EMBEDDING_MAX_BATCH),
            model_version=b.extra_params.get("model_version", m.name),
            task=task_default,
        )

    async def resolve_prompt(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        template_key: str,
        version: int | None = None,
    ) -> PromptTemplate:
        # Try per-bot first, fallback tenant default.
        row: PromptTemplateRow | None = await self._repo.get_prompt_template(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            template_key=template_key,
            version=version,
        )
        if row is None:
            row = await self._repo.get_prompt_template(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                template_key=template_key,
                version=version,
            )
        if row is None:
            raise InvariantViolation(
                f"No active prompt template '{template_key}' for bot {record_bot_id}",
            )
        return PromptTemplate(
            template_key=row.template_key,
            version=row.version,
            jinja_source=row.jinja_source,
            required_vars=frozenset(row.required_vars),
            model_hint=row.model_hint,
        )

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

    # ----- Runtime-config (L1 LRU + DB fallback) ----------------------------
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

    async def resolve_runtime(
        self,
        record_tenant_id: object,
        record_bot_id: object,
        purpose: str,
        **_kw: object,
    ) -> ModelRuntimeConfig:
        """Resolve ModelRuntimeConfig via L1 → DB."""
        key = self._runtime_cache_key(record_tenant_id, record_bot_id, purpose)

        cfg = self._l1_get(key)
        if cfg is not None:
            return cfg

        bindings = await self._repo.list_bindings(
            record_tenant_id=record_tenant_id,  # type: ignore[arg-type]
            record_bot_id=record_bot_id,  # type: ignore[arg-type]
            purpose=purpose,
        )
        # Fallback: nếu không có binding cho purpose cụ thể → dùng llm_primary.
        # Per-bot opt-out is implicit: not seeding the cheap-purpose row keeps
        # the bot on PRIMARY automatically (no flag toggle needed).
        if not bindings and purpose != DEFAULT_LLM_PURPOSE_PRIMARY:
            bindings = await self._repo.list_bindings(
                record_tenant_id=record_tenant_id,  # type: ignore[arg-type]
                record_bot_id=record_bot_id,  # type: ignore[arg-type]
                purpose=DEFAULT_LLM_PURPOSE_PRIMARY,
            )
        if not bindings:
            raise InvariantViolation(
                f"No binding for tenant={record_tenant_id} bot={record_bot_id} purpose={purpose}",
            )
        primary = sorted(bindings, key=lambda b: b.rank)[0]
        model = await self._repo.get_model(primary.model_id)
        if model is None:
            raise InvariantViolation(f"Model {primary.model_id} missing")
        provider = await self._repo.get_provider(model.provider_id)
        if provider is None:
            raise InvariantViolation(f"Provider {model.provider_id} missing")

        cfg = await self._build_runtime(
            binding=primary, model=model, provider=provider, purpose=purpose,
        )
        self._l1_put(key, cfg)

        # Best-effort L2 write (masked payload only — no api_key)
        try:
            await self._cache.set(
                key,
                json.dumps(cfg.mask()).encode("utf-8"),
                ttl_s=self._l2_ttl,
            )
        except Exception:  # noqa: BLE001 — cache write is non-critical; next call will rebuild from DB. Log so a dying Redis is visible.
            logger.debug("model_resolver_cache_set_failed", key=key, exc_info=True)

        return cfg

    async def preview_runtime(
        self,
        *,
        model_id: object,
        bot_id: object | None = None,
    ) -> ModelRuntimeConfig | None:
        """Build a preview ModelRuntimeConfig for admin UI.

        Looks up optional binding for (model_id, bot_id). If present, uses its
        temperature/top_p/max_tokens. Otherwise falls back to model defaults.
        """
        model = await self._repo.get_model(model_id)  # type: ignore[arg-type]
        if model is None:
            return None
        provider = await self._repo.get_provider(model.provider_id)
        if provider is None:
            return None

        binding: BindingRow | None = None
        if bot_id is not None:
            try:
                bindings = await self._repo.list_bindings(
                    record_tenant_id=None,  # type: ignore[arg-type]
                    record_bot_id=bot_id,  # type: ignore[arg-type]
                    purpose="llm_primary",
                )
                for b in bindings:
                    if str(b.model_id) == str(model.id):
                        binding = b
                        break
            except (AttributeError, TypeError):
                # Programmer bug — re-raise rather than silent default fallback.
                logger.exception("binding_lookup_programmer_bug", bot_id=str(bot_id))
                raise
            except Exception:  # noqa: BLE001
                # DB read failure — degrade to None (default binding).
                logger.warning("binding_lookup_failed", bot_id=str(bot_id), exc_info=True)
                binding = None

        return await self._build_runtime(
            binding=binding,
            model=model,
            provider=provider,
            purpose=binding.purpose if binding else "preview",
        )

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

    def resolve_cascade_runtime(
        self,
        complexity_score: float,
        bot_config: dict[str, object] | None = None,
        *,
        config_getter: object | None = None,
    ) -> str:
        """Map a query-complexity score to a tier model name.

        Cascade routing semantics (T1-Smartness + T2-CostPerf):
        - ``score < T_LOW`` → cheap-tier model (low-cost answer LLM).
        - ``T_LOW ≤ score < T_HIGH`` → bot's existing default answer model.
        - ``score ≥ T_HIGH`` → premium-tier model (complex-query answer LLM).

        The three model names + the two thresholds resolve from
        ``system_config`` so bot owners tune the band without a redeploy.
        Per-bot binding overrides (``bot_config`` dict) take priority over
        platform-level ``system_config`` defaults, mirroring the
        ``per-bot binding → system_config + ai_models → NullObject``
        fallback chain mandated by CLAUDE.md
        (feedback_resolver_must_fallback_system_config). When neither the
        binding nor ``system_config`` carries a value the helper returns
        an empty string — callers MUST treat that as "no cascade target"
        and stick with the bot's current model (NullObject contract).

        Pure / synchronous so the caller (orchestration node) can invoke
        it on the hot path without an extra ``await`` boundary. DB-bound
        config IO happens inside ``get_boot_config`` which carries its
        own short TTL cache.

        Args:
            complexity_score: 0.0-1.0 score from the query_complexity
                classifier. NaN / negative / >1.0 inputs are clamped to
                the valid band so a misbehaving caller cannot route to
                an undefined tier.
            bot_config: Optional ``bots.plan_limits`` / threshold-overrides
                dict for per-bot model overrides (keys
                ``cascade_low_model`` / ``default_answer_model`` /
                ``cascade_high_model``). Pass an empty dict when the bot
                has no overrides.
            config_getter: Optional injection seam mirroring
                ``query_complexity.classify_query_complexity`` —
                ``Callable[[str, Any], Any]`` with the signature of
                ``get_boot_config(key, default)``. Tests pass a stub.

        Returns:
            Model-name string (e.g. ``"claude-haiku-4-5-20251001"``) or
            ``""`` when no model is configured for the chosen tier
            (NullObject — caller falls back to current model). The
            string is matched against ``ai_models.name`` SSoT
            downstream.
        """
        # Lazy import so the module-level ``get_boot_config`` import
        # stays where it belongs and tests can inject a stub without
        # monkey-patching the resolver module.
        if config_getter is None:
            from ragbot.shared.bootstrap_config import (  # noqa: PLC0415
                get_boot_config,
            )
            getter = get_boot_config
        else:
            getter = config_getter  # type: ignore[assignment]

        # Clamp the input score into [0.0, 1.0] so threshold math is
        # well-defined even when the upstream classifier mis-emits.
        try:
            score = float(complexity_score)
        except (TypeError, ValueError):
            score = 0.0
        if score != score:  # NaN guard — float("nan") is the only x != x
            score = 0.0
        if score < 0.0:
            score = 0.0
        elif score > 1.0:
            score = 1.0

        # Threshold knobs: system_config → constants fallback.
        try:
            t_low = float(getter("cascade_t_low", DEFAULT_CASCADE_T_LOW))
        except (TypeError, ValueError):
            t_low = DEFAULT_CASCADE_T_LOW
        try:
            t_high = float(getter("cascade_t_high", DEFAULT_CASCADE_T_HIGH))
        except (TypeError, ValueError):
            t_high = DEFAULT_CASCADE_T_HIGH
        # Misconfigured band (low ≥ high) collapses the cheap tier so we
        # never accidentally promote ambiguous queries to premium.
        if t_low > t_high:
            t_low = t_high

        cfg = bot_config or {}

        def _pick(per_bot_key: str, system_key: str) -> str:
            """Per-bot binding → system_config → empty string (NullObject)."""
            per_bot = cfg.get(per_bot_key)
            if isinstance(per_bot, str) and per_bot.strip():
                return per_bot.strip()
            sys_val = getter(system_key, "")
            if isinstance(sys_val, str) and sys_val.strip():
                return sys_val.strip()
            return ""

        if score < t_low:
            return _pick("cascade_low_model", "cascade_low_model")
        if score < t_high:
            return _pick("default_answer_model", "default_answer_model")
        return _pick("cascade_high_model", "cascade_high_model")

    # ----- Internals --------------------------------------------------------
    @staticmethod
    def _intent_to_purpose(intent: LLMIntent) -> str:
        # Ingest contextual-retrieval / narrate enrichment routes to its own
        # purpose so it can run a cheaper SHARED-DEFAULT model than the answer
        # LLM (extractive task, highest call volume). Every OTHER intent stays
        # on llm_primary — the query answer path is byte-for-byte unchanged.
        # resolve_llm falls back to llm_primary when a bot has no binding for
        # the enrichment purpose, so this never breaks resolution and a bot can
        # still override by adding its own enrichment binding (custom > shared).
        if intent in _ENRICHMENT_INTENTS:
            return DEFAULT_LLM_PURPOSE_ENRICHMENT
        return BindingPurpose.LLM_PRIMARY.value

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

    async def _resolve_provider_runtime(
        self,
        provider: ProviderRow,
    ) -> ProviderRuntime:
        """Build a ``ProviderRuntime`` (api_key resolved + provider metadata)."""
        api_key = ""
        if self._secrets is not None:
            encrypted = (provider.metadata or {}).get("api_key_encrypted")
            ref = provider.credentials_vault_path
            try:
                api_key = await self._secrets.resolve(ref, encrypted)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "secrets_resolve_failed",
                    provider_code=provider.code,
                    provider_name=provider.name,
                )
                api_key = ""

        pmeta = provider.metadata or {}
        return ProviderRuntime(
            code=provider.code,
            base_url=provider.base_url,
            api_key=api_key,
            timeout_ms=int(pmeta.get("timeout_ms", DEFAULT_PROVIDER_TIMEOUT_MS)),
            connect_timeout_ms=int(pmeta.get("connect_timeout_ms", DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS)),
            max_retries=int(pmeta.get("max_retries", DEFAULT_PROVIDER_MAX_RETRIES)),
            max_concurrent=int(pmeta.get("max_concurrent", DEFAULT_PROVIDER_MAX_CONCURRENT)),
            region=pmeta.get("region"),
        )

    async def _build_runtime(
        self,
        *,
        binding: BindingRow | None,
        model: ModelRow,
        provider: ProviderRow,
        purpose: str,
    ) -> ModelRuntimeConfig:
        provider_rt = await self._resolve_provider_runtime(provider)
        mmeta = model.metadata or {}
        params = GenerationParams(
            temperature=float(binding.temperature) if binding else float(
                getattr(model, "default_temperature", 0.0) or 0.0,
            ),
            top_p=float(binding.top_p) if binding else float(
                getattr(model, "default_top_p", 1.0) or 1.0,
            ),
            max_tokens=int(binding.max_tokens) if binding else int(
                getattr(model, "default_max_tokens", model.max_output_tokens)
                or model.max_output_tokens,
            ),
        )
        pricing = Pricing(
            input_per_1k_usd=Decimal(str(model.input_price_per_1k_usd)),
            output_per_1k_usd=Decimal(str(model.output_price_per_1k_usd)),
            cached_input_per_1k_usd=(
                Decimal(str(mmeta["cached_input_per_1k_usd"]))
                if "cached_input_per_1k_usd" in mmeta
                else None
            ),
        )
        caps = Capabilities(
            supports_tool_use=bool(model.supports_tools),
            supports_vision=bool(model.supports_vision),
            supports_json_mode=bool(model.supports_json_mode),
            supports_caching=bool(mmeta.get("supports_caching", False)),
            supports_streaming=bool(model.supports_streaming),
            supports_reasoning=bool(mmeta.get("supports_reasoning", False)),
        )

        hash_payload = {
            "provider_code": provider_rt.code,
            "base_url": provider_rt.base_url,
            "wire_model_id": model.name,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
            "context_window": model.context_window,
            "input_price": str(pricing.input_per_1k_usd),
            "output_price": str(pricing.output_per_1k_usd),
            "binding_id": str(binding.id) if binding else None,
            "binding_version": binding.version if binding else None,
        }
        version_hash = compute_version_hash(hash_payload)

        fallback_model_row_id: UUID | None = None
        fallback_wire_model_id: str | None = None
        fallback_provider_rt: ProviderRuntime | None = None
        fb_id = getattr(binding, "record_fallback_model_id", None) if binding else None
        if fb_id is not None:
            try:
                fb_model = await self._repo.get_model(fb_id)
                if fb_model is not None:
                    fb_provider = await self._repo.get_provider(fb_model.provider_id)
                    if fb_provider is not None:
                        fallback_provider_rt = await self._resolve_provider_runtime(fb_provider)
                        fallback_model_row_id = fb_model.id
                        fallback_wire_model_id = format_litellm_model(
                            fb_model.name, fb_provider,
                        )
            except (AttributeError, KeyError, ValueError):
                logger.warning(
                    "fallback_model_resolve_failed",
                    binding_id=str(binding.id) if binding else None,
                    record_fallback_model_id=str(fb_id),
                )

        return ModelRuntimeConfig(
            model_row_id=model.id,
            binding_id=binding.id if binding else None,
            purpose=purpose,
            kind=model.kind,
            provider=provider_rt,
            wire_model_id=model.name,
            litellm_name=format_litellm_model(model.name, provider),
            context_window=int(model.context_window),
            max_output_tokens=int(model.max_output_tokens),
            embedding_dimension=(
                # Per-bot binding wins over model metadata; falls back to model column then None.
                int(
                    (binding.extra_params.get("dimension") if binding else None)
                    or mmeta.get("dimension")
                    or getattr(model, "embedding_dimension", None)
                    or 0,
                ) or None
                if model.kind == "embedding"
                else None
            ),
            params=params,
            pricing=pricing,
            capabilities=caps,
            quality_tier=_quality_tier_from_model(model),
            version_hash=version_hash,
            loaded_at=self._clock.now(),
            fallback_model_row_id=fallback_model_row_id,
            fallback_wire_model_id=fallback_wire_model_id,
            fallback_provider=fallback_provider_rt,
        )

    def _select_primary_with_ab(
        self,
        bindings: list[BindingRow],
        conversation_id: ConversationId | None,
    ) -> BindingRow:
        """Pick primary binding (rank=0). If multiple variants, route by hash."""
        primaries = [b for b in bindings if b.rank == 0]
        if not primaries:
            raise InvariantViolation("No primary binding (rank=0) found")
        if len(primaries) == 1:
            return primaries[0]

        seed_str = str(conversation_id or "no-conv")
        seed = int.from_bytes(
            hashlib.sha256(seed_str.encode()).digest()[:4],
            "big",
        )
        bucket = seed % 100
        accumulated = 0
        for b in primaries:
            accumulated += b.weight
            if bucket < accumulated:
                return b
        return primaries[-1]

    def _binding_to_spec(self, b: BindingRow, cache: _CachedBindings) -> LLMSpec:
        m = cache.models_by_id[str(b.model_id)]
        p = cache.providers_by_id[str(m.provider_id)]
        litellm_name = format_litellm_model(m.name, p)

        fallback_chain = [
            format_litellm_model(
                cache.models_by_id[str(fb.model_id)].name,
                cache.providers_by_id[
                    str(cache.models_by_id[str(fb.model_id)].provider_id)
                ],
            )
            for fb in cache.bindings
            if fb.rank > b.rank and fb.purpose == b.purpose
        ]
        cost_tier = self._cost_tier(m)

        return LLMSpec(
            binding_id=b.id,
            model_name=litellm_name,
            provider=p.code,
            temperature=b.temperature,
            max_tokens=b.max_tokens,
            top_p=b.top_p,
            fallback_chain=fallback_chain,
            cost_tier=cost_tier,
            variant=b.variant,
            extra_params=dict(b.extra_params),
        )

    @staticmethod
    def _cost_tier(m: ModelRow) -> str:
        out = float(m.output_price_per_1k_usd)
        if out >= 10.0:
            return "premium"
        if out >= 1.0:
            return "mid"
        return "cheap"

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


# --- helpers (keep dataclass round-trip simple) ------------------------------
__all__ = [
    "ModelResolverService",
    "resolve_purpose_for_intent",
    "to_embedding_spec",
    "to_llm_spec",
    "to_reranker_spec",
    "format_litellm_model",
    "_CachedBindings",
]
