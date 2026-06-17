"""ModelRuntimeConfig — resolved runtime spec for a (tenant, bot, purpose).

Frozen dataclass; ``mask()`` for admin responses (api_key redacted);
``compute_version_hash()`` deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from ragbot.shared.constants import (
    DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS,
    DEFAULT_PROVIDER_MAX_CONCURRENT,
    DEFAULT_PROVIDER_MAX_RETRIES,
    DEFAULT_PROVIDER_TIMEOUT_MS,
)


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    code: str
    base_url: str
    api_key: str  # plain (resolved) — NEVER log
    timeout_ms: int = DEFAULT_PROVIDER_TIMEOUT_MS
    connect_timeout_ms: int = DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS
    max_retries: int = DEFAULT_PROVIDER_MAX_RETRIES
    max_concurrent: int = DEFAULT_PROVIDER_MAX_CONCURRENT
    region: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationParams:
    temperature: float
    top_p: float
    max_tokens: int


@dataclass(frozen=True, slots=True)
class Pricing:
    input_per_1k_usd: Decimal
    output_per_1k_usd: Decimal
    cached_input_per_1k_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    supports_tool_use: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    supports_caching: bool = False
    supports_streaming: bool = True
    supports_reasoning: bool = False


@dataclass(frozen=True, slots=True)
class ModelRuntimeConfig:
    model_row_id: UUID
    binding_id: UUID | None
    purpose: str
    kind: str                        # chat|embedding|reranker
    provider: ProviderRuntime
    wire_model_id: str
    litellm_name: str                # f"{provider.code}/{wire_model_id}"
    context_window: int
    max_output_tokens: int
    embedding_dimension: int | None
    params: GenerationParams
    pricing: Pricing
    capabilities: Capabilities
    quality_tier: str
    version_hash: str                # sha256 deterministic
    loaded_at: datetime
    # Optional same-tier failover hop. Populated by the resolver from the
    # binding row's ``record_fallback_model_id``; the LLM router consults
    # these fields when the primary call hits a circuit-breaker open or a
    # retryable LiteLLM error. ``None`` on any of the three = no failover
    # configured (per-bot opt-out, terminal primary call).
    fallback_model_row_id: UUID | None = None
    fallback_wire_model_id: str | None = None
    fallback_provider: ProviderRuntime | None = None

    def mask(self) -> dict:
        """Serialize for admin response with api_key masked."""
        ak = self.provider.api_key or ""
        masked_key = "sk-***" + (ak[-4:] if len(ak) > 4 else "***")
        return {
            "model_row_id": str(self.model_row_id),
            "binding_id": str(self.binding_id) if self.binding_id else None,
            "purpose": self.purpose,
            "kind": self.kind,
            "provider": {
                "code": self.provider.code,
                "base_url": self.provider.base_url,
                "api_key": masked_key,
                "timeout_ms": self.provider.timeout_ms,
                "max_retries": self.provider.max_retries,
            },
            "wire_model_id": self.wire_model_id,
            "litellm_name": self.litellm_name,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "params": {
                "temperature": self.params.temperature,
                "top_p": self.params.top_p,
                "max_tokens": self.params.max_tokens,
            },
            "pricing": {
                "input_per_1k_usd": str(self.pricing.input_per_1k_usd),
                "output_per_1k_usd": str(self.pricing.output_per_1k_usd),
                "cached_input_per_1k_usd": (
                    str(self.pricing.cached_input_per_1k_usd)
                    if self.pricing.cached_input_per_1k_usd is not None
                    else None
                ),
            },
            "capabilities": {
                f: getattr(self.capabilities, f)
                for f in Capabilities.__dataclass_fields__
            },
            "quality_tier": self.quality_tier,
            "version_hash": self.version_hash,
            "loaded_at": self.loaded_at.isoformat(),
        }


def compute_version_hash(payload: dict) -> str:
    """Deterministic sha256 hash of config dict (sort keys, exclude api_key)."""
    safe = {k: v for k, v in payload.items() if k != "api_key"}
    return hashlib.sha256(
        json.dumps(safe, sort_keys=True, default=str).encode(),
    ).hexdigest()


__all__ = [
    "Capabilities",
    "GenerationParams",
    "ModelRuntimeConfig",
    "Pricing",
    "ProviderRuntime",
    "compute_version_hash",
]
