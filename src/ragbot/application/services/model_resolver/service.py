"""DB-driven AI strategy resolver — main service class.

Strangler split 2026-06-19: cache -> _cache_mixin, binding/spec -> _binding_mixin,
pure helpers/types -> _helpers. resolve_* public API ở đây.
"""
from __future__ import annotations

import asyncio
import hashlib
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
from ragbot.application.ports.system_config_reader_port import (
    SystemConfigReaderPort,
)
from ragbot.shared.constants import (
    AI_MODEL_KIND_EMBEDDING,
    AI_MODEL_KIND_LLM,
    AI_MODEL_KIND_RERANKER,
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
    SYSTEM_CONFIG_KEY_EMBEDDING_MODEL,
    SYSTEM_CONFIG_KEY_LLM_DEFAULT_MODEL,
    SYSTEM_CONFIG_KEY_RERANKER_MODEL,
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
from ragbot.application.services.model_resolver._cache_mixin import CacheMixin
from ragbot.application.services.model_resolver._binding_mixin import BindingMixin


class ModelResolverService(CacheMixin, BindingMixin):
    """Resolve model specs from DB with caching + A/B variant + cascade."""

    def __init__(
        self,
        repo: AIConfigRepositoryPort,
        cache: CachePort,
        clock: Clock,
        *,
        cache_ttl_s: int = DEFAULT_MODEL_RESOLVER_CACHE_TTL,
        secrets_port: SecretsPort | None = None,
        system_config: SystemConfigReaderPort | None = None,
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
        # Read-only system_config SSoT (Redis-cached, ~5-min TTL). When a bot
        # has no per-bot binding for a purpose the resolver reads the realtime
        # platform-default model NAME here instead of raising — honouring the
        # ``per-bot binding → system_config + ai_models → NullObject`` chain.
        # ``None`` keeps the legacy raise-on-no-binding behaviour (tests /
        # callers that don't wire the reader).
        self._sysconfig = system_config
        self._l1_ttl = l1_ttl_s
        self._l2_ttl = l2_ttl_s
        self._l1_max = l1_max_size
        self._l1: OrderedDict[str, tuple[ModelRuntimeConfig, float]] = OrderedDict()
        self._last_bootstrap_at: datetime | None = None

    async def _system_config_default_model(
        self,
        *,
        config_key: str,
        kind: str,
    ) -> tuple[ModelRow, ProviderRow] | None:
        """Resolve the platform-default ``(ModelRow, ProviderRow)`` for ``kind``.

        Reads the model NAME from ``system_config[config_key]`` (Redis-cached
        SSoT — realtime within the system_config TTL) then resolves the
        matching ENABLED ``ai_models`` row of the required ``kind`` plus its
        provider. The ``kind`` filter is the cross-kind guard: an
        ``embedding`` fallback can NEVER pick an LLM row (provider 404).

        Returns ``None`` (NullObject — caller raises) when the reader is not
        wired, the config value is empty, or no enabled model of that kind
        carries the configured name. The model NAME is read fresh on every
        call, so an operator ``UPDATE system_config`` takes effect on the
        next resolve within the Redis TTL — the resolved spec is NOT pinned
        in the resolver's own (longer-TTL) binding cache.
        """
        if self._sysconfig is None:
            return None
        raw_name = await self._sysconfig.get(config_key, None)
        model_name = str(raw_name).strip() if raw_name else ""
        if not model_name:
            return None
        # Enabled models of the required kind only — never cross-kind.
        models = await self._repo.list_models(kind=kind, enabled_only=True)
        model = next((m for m in models if m.name == model_name), None)
        if model is None:
            logger.warning(
                "system_config_default_model_missing",
                config_key=config_key,
                kind=kind,
                model_name=model_name,
            )
            return None
        provider = await self._repo.get_provider(model.provider_id)
        if provider is None:
            logger.warning(
                "system_config_default_provider_missing",
                config_key=config_key,
                model_name=model_name,
                provider_id=str(model.provider_id),
            )
            return None
        return model, provider

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
            # No per-bot binding for ANY LLM purpose → follow the realtime
            # platform default in system_config (Redis-cached SSoT). Updating
            # ``system_config.llm_default_model`` swaps the answer model for
            # every binding-less bot with no app restart. Only the rank-0
            # primary is resolvable this way (no fallback chain); higher
            # ``iter`` still raises (no shared-default failover configured).
            fallback = await self._system_config_default_model(
                config_key=SYSTEM_CONFIG_KEY_LLM_DEFAULT_MODEL,
                kind=AI_MODEL_KIND_LLM,
            )
            if fallback is not None and iter == 0:
                model, provider = fallback
                logger.info(
                    "llm_resolved_from_system_config_default",
                    record_bot_id=str(record_bot_id),
                    model_name=model.name,
                    provider=provider.code,
                )
                return self._llm_spec_from_model(model, provider)
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
            # No per-bot binding → follow the realtime platform default in
            # system_config (Redis-cached SSoT). Mirrors the LLM + embedding
            # fallback and ``reranker_resolver._lookup_platform_default``.
            # Updating ``system_config.reranker_model`` swaps the reranker for
            # every binding-less bot with no app restart.
            fallback = await self._system_config_default_model(
                config_key=SYSTEM_CONFIG_KEY_RERANKER_MODEL,
                kind=AI_MODEL_KIND_RERANKER,
            )
            if fallback is not None:
                model, provider = fallback
                logger.info(
                    "reranker_resolved_from_system_config_default",
                    record_bot_id=str(record_bot_id),
                    model_name=model.name,
                    provider=provider.code,
                )
                return self._reranker_spec_from_model(model, provider)
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
        # Cross-kind guard: ``embedding`` / ``rerank`` are NOT LLM purposes —
        # they MUST NEVER fall back to ``llm_primary`` (that would hand an LLM
        # model to the embedder/reranker → provider 404). When a bot has no
        # binding for these, follow the realtime kind-matched platform default
        # in system_config (``embedding_model`` / ``reranker_model``).
        _kind_default_key = {
            BindingPurpose.EMBEDDING.value: (
                SYSTEM_CONFIG_KEY_EMBEDDING_MODEL,
                AI_MODEL_KIND_EMBEDDING,
            ),
            BindingPurpose.RERANK.value: (
                SYSTEM_CONFIG_KEY_RERANKER_MODEL,
                AI_MODEL_KIND_RERANKER,
            ),
        }.get(purpose)

        if not bindings and _kind_default_key is not None:
            config_key, kind = _kind_default_key
            fallback = await self._system_config_default_model(
                config_key=config_key, kind=kind,
            )
            if fallback is None:
                raise InvariantViolation(
                    f"No {purpose} binding for tenant={record_tenant_id} "
                    f"bot={record_bot_id} and no system_config default",
                )
            model, provider = fallback
            logger.info(
                "runtime_resolved_from_system_config_default",
                record_bot_id=str(record_bot_id),
                purpose=purpose,
                model_name=model.name,
                provider=provider.code,
            )
            cfg = await self._build_runtime(
                binding=None, model=model, provider=provider, purpose=purpose,
            )
            # NOT L1-cached: a kind-default resolution must reflect a
            # system_config flip within the Redis TTL — the model NAME is read
            # fresh each call (the binding-less path is cold by design).
            return cfg

        # LLM cost-routing fallback: a missing cheap-purpose row (e.g.
        # ``llm_factoid``) transparently reuses ``llm_primary`` (custom >
        # shared, per-bot opt-out by not seeding the row). ``llm_primary``
        # itself has no further binding fallback here.
        if not bindings and purpose != DEFAULT_LLM_PURPOSE_PRIMARY:
            bindings = await self._repo.list_bindings(
                record_tenant_id=record_tenant_id,  # type: ignore[arg-type]
                record_bot_id=record_bot_id,  # type: ignore[arg-type]
                purpose=DEFAULT_LLM_PURPOSE_PRIMARY,
            )
        if not bindings:
            # Last resort for an LLM purpose with no per-bot binding at all →
            # realtime system_config.llm_default_model (kind-matched to LLM).
            fallback = await self._system_config_default_model(
                config_key=SYSTEM_CONFIG_KEY_LLM_DEFAULT_MODEL,
                kind=AI_MODEL_KIND_LLM,
            )
            if fallback is None:
                raise InvariantViolation(
                    f"No binding for tenant={record_tenant_id} bot={record_bot_id} purpose={purpose}",
                )
            model, provider = fallback
            logger.info(
                "runtime_resolved_from_system_config_default",
                record_bot_id=str(record_bot_id),
                purpose=purpose,
                model_name=model.name,
                provider=provider.code,
            )
            return await self._build_runtime(
                binding=None, model=model, provider=provider, purpose=purpose,
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

        # No L2 (Redis) write here: the runtime path reads ONLY L1 (`_l1_get`
        # above + bootstrap `_l1_put`). The masked payload (no api_key) cannot
        # rehydrate a runtime config, and `_get_cached`'s `_cache.get` reads a
        # different namespace (`ai_cfg:*`), never `model_runtime:*` — so a write
        # here was a dead side-effect (json serialize + Redis round-trip) that
        # nothing ever read back.
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

    # ━━━ DEAD CODE: resolve_fallback_chain() — 0 caller (verified 2026-06-19), commented để giới hạn scope (an toàn xoá) ━━━
    # async def resolve_fallback_chain(
        # self,
        # record_bot_id: BotId,
        # *,
        # record_tenant_id: TenantId,
        # intent: LLMIntent,
    # ) -> list[LLMSpec]:
        # purpose = self._intent_to_purpose(intent)
        # cached = await self._get_cached(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, purpose=purpose)
        # sorted_bs = sorted(cached.bindings, key=lambda b: b.rank)
        # return [self._binding_to_spec(b, cached) for b in sorted_bs]
    # ━━━ END DEAD resolve_fallback_chain() ━━━

    # ━━━ DEAD CODE: resolve_prompt() — 0 caller (verified 2026-06-19), commented để giới hạn scope (an toàn xoá) ━━━
    # async def resolve_prompt(
        # self,
        # record_bot_id: BotId,
        # *,
        # record_tenant_id: TenantId,
        # template_key: str,
        # version: int | None = None,
    # ) -> PromptTemplate:
        # # Try per-bot first, fallback tenant default.
        # row: PromptTemplateRow | None = await self._repo.get_prompt_template(
            # record_tenant_id=record_tenant_id,
            # record_bot_id=record_bot_id,
            # template_key=template_key,
            # version=version,
        # )
        # if row is None:
            # row = await self._repo.get_prompt_template(
                # record_tenant_id=record_tenant_id,
                # record_bot_id=None,
                # template_key=template_key,
                # version=version,
            # )
        # if row is None:
            # raise InvariantViolation(
                # f"No active prompt template '{template_key}' for bot {record_bot_id}",
            # )
        # return PromptTemplate(
            # template_key=row.template_key,
            # version=row.version,
            # jinja_source=row.jinja_source,
            # required_vars=frozenset(row.required_vars),
            # model_hint=row.model_hint,
        # )
    # ━━━ END DEAD resolve_prompt() ━━━
