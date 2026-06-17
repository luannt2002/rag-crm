"""Pure helpers for model resolution — wire-format, intent routing, cache (de)serialization, spec converters.

Extracted from the model_resolver god-file. No service state — re-exported by model_resolver/__init__ so existing imports stay valid.
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




@dataclass(frozen=True, slots=True)
class _CachedBindings:
    bindings: tuple[BindingRow, ...]
    models_by_id: dict[str, ModelRow]
    providers_by_id: dict[str, ProviderRow]
    cached_at: float


def _quality_tier_from_model(m: ModelRow) -> str:
    out = float(m.output_price_per_1k_usd)
    if out >= 10.0:
        return "premium"
    if out >= 1.0:
        return "mid"
    return "cheap"


def format_litellm_model(model_name: str, provider: ProviderRow) -> str:
    """Build the LiteLLM wire model name for ``model_name`` on ``provider``.

    Strategy + DI replacement for the historical
    ``f"{provider.code}/{model_name}"`` literal (and its ``if code ==
    "openai"`` exception). Behaviour is now controlled by the
    ``ai_providers.requires_prefix`` DB column (alembic 010e):

    - **Already-prefixed model name** (``"vertex_ai/gemini-1.5-pro"``) →
      passthrough. LiteLLM treats an explicit prefix as authoritative; we
      must not double-prefix or strip operator intent.
    - **``provider.requires_prefix=False``** (OpenAI / Anthropic native) →
      bare ``model_name``. LiteLLM accepts unprefixed names for these.
    - **``provider.requires_prefix=True``** (Cohere / Jina / Voyage /
      ZeroEntropy / …, default for new providers) → ``{provider.code}/
      {model_name}``.

    Adding a new provider that needs / does not need a prefix is a SQL
    ``UPDATE ai_providers SET requires_prefix = ...`` — no Python edit
    required.

    @param model_name: raw model identifier from ``ai_models.name``.
    @param provider: resolved ``ProviderRow`` for the model.
    @return: LiteLLM wire name suitable for ``litellm.acompletion(model=...)``.
    """
    if "/" in model_name:
        return model_name
    if not provider.requires_prefix:
        return model_name
    return f"{provider.code}/{model_name}"


def resolve_purpose_for_intent(intent: str | None) -> str:
    """Map intent → LLM binding purpose for cost-aware routing.

    Returns the cheap-purpose binding name when the intent is in the
    DEFAULT_CHEAP_INTENT_PURPOSES mapping (factoid / chitchat / OOS-style
    intents); otherwise returns ``llm_primary``.

    The resolver itself (``resolve_runtime``) falls back to ``llm_primary``
    when the cheap-purpose binding row is absent for the bot — so this
    function returning a cheap purpose never breaks per-bot opt-out.

    Bot-owner ops to seed cheap binding (no auto-migration shipped, keeping
    the platform domain-neutral / model-literal-free)::

        INSERT INTO bot_model_bindings
          (record_bot_id, purpose, record_model_id, ...)
        VALUES
          ('<bot-uuid>', 'llm_factoid',  '<cheap-model-uuid>', ...),
          ('<bot-uuid>', 'llm_chitchat', '<cheap-model-uuid>', ...),
          ('<bot-uuid>', 'llm_oos',      '<cheap-model-uuid>', ...);

    Skip those rows for any bot that should keep PRIMARY for everything.
    """
    if not intent:
        return DEFAULT_LLM_PURPOSE_PRIMARY
    return DEFAULT_CHEAP_INTENT_PURPOSES.get(
        intent.lower(), DEFAULT_LLM_PURPOSE_PRIMARY,
    )


def _dataclass_dict(obj: object) -> dict[str, object]:
    from dataclasses import asdict

    d = asdict(obj)  # type: ignore[arg-type]
    for k, v in list(d.items()):
        if hasattr(v, "hex") and not isinstance(v, bytes | str):  # UUID
            d[k] = str(v)
        elif isinstance(v, tuple):
            d[k] = list(v)
    return d


def _dict_to_dataclass(cls: type, raw: dict[str, object]) -> object:  # type: ignore[type-arg]
    from dataclasses import fields
    from uuid import UUID

    kwargs: dict[str, object] = {}
    for f in fields(cls):
        val = raw.get(f.name)
        if val is None:
            kwargs[f.name] = None
            continue
        if "id" in f.name and isinstance(val, str) and len(val) == 36:
            try:
                kwargs[f.name] = UUID(val)
                continue
            except ValueError:  # pragma: no cover
                pass
        kwargs[f.name] = val
    return cls(**kwargs)  # type: ignore[call-arg]


# ---- Spec adapters from ModelRuntimeConfig --------------------------------


def to_llm_spec(cfg: ModelRuntimeConfig) -> LLMSpec:
    return LLMSpec(
        binding_id=cfg.binding_id or uuid4(),
        model_name=cfg.litellm_name,
        provider=cfg.provider.code,
        temperature=cfg.params.temperature,
        max_tokens=cfg.params.max_tokens,
        top_p=cfg.params.top_p,
        fallback_chain=[],
        cost_tier=cfg.quality_tier if cfg.quality_tier in ("cheap", "mid", "premium") else "mid",
        variant=None,
        extra_params={},
    )


def to_embedding_spec(cfg: ModelRuntimeConfig) -> EmbeddingSpec:
    # Default to passage adapter — asymmetric models (Jina v3) require an
    # explicit task. Query path overrides via ``model_copy`` at call site.
    return EmbeddingSpec(
        binding_id=cfg.binding_id or uuid4(),
        model_name=cfg.litellm_name,
        provider=cfg.provider.code,
        dimension=cfg.embedding_dimension or DEFAULT_RERANKER_EMBEDDING_DIM,
        max_batch=DEFAULT_EMBEDDING_MAX_BATCH,
        model_version=cfg.wire_model_id,
        task=DEFAULT_EMBEDDING_TASK_PASSAGE,
    )


def to_reranker_spec(cfg: ModelRuntimeConfig) -> RerankerSpec:
    return RerankerSpec(
        binding_id=cfg.binding_id or uuid4(),
        model_name=cfg.wire_model_id,
        provider=cfg.provider.code,
        top_n=DEFAULT_RERANK_TOP_N,
        batch_size=DEFAULT_RERANK_TOP_N * 10,
    )


__all__ = [
    "_CachedBindings",
    "_quality_tier_from_model",
    "format_litellm_model",
    "resolve_purpose_for_intent",
    "_dataclass_dict",
    "_dict_to_dataclass",
    "to_llm_spec",
    "to_embedding_spec",
    "to_reranker_spec",
]
