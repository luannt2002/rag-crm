"""ModelResolverService — binding/spec/runtime-build concern. Tách từ __init__.py 2026-06-19."""
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


class BindingMixin:
    """ModelResolverService — binding/spec/runtime-build concern. Tách từ __init__.py 2026-06-19."""

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

    # ── Specs built from a bare (model, provider) — no per-bot binding ──
    # The system_config platform-default fallback path: a bot WITHOUT a
    # binding row resolves these from the SSoT model NAME. Generation params
    # use the spec DTO defaults (no binding row to read temperature/top_p
    # from); a bot wanting custom params seeds its own binding (custom >
    # shared). ``binding_id`` is a synthetic uuid4 — there is no real
    # ``bot_model_bindings`` row behind a platform-default resolution.

    def _llm_spec_from_model(self, m: ModelRow, p: ProviderRow) -> LLMSpec:
        return LLMSpec(
            binding_id=uuid4(),
            model_name=format_litellm_model(m.name, p),
            provider=p.code,
            fallback_chain=[],
            cost_tier=self._cost_tier(m),
            variant=None,
            extra_params={},
        )

    @staticmethod
    def _reranker_spec_from_model(m: ModelRow, p: ProviderRow) -> RerankerSpec:
        return RerankerSpec(
            binding_id=uuid4(),
            model_name=m.name,
            provider=p.code,
        )
