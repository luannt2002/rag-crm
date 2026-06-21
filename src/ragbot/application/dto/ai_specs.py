"""AI strategy specs returned by ``ModelResolverService`` to use cases / workers."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ragbot.shared.constants import (
    DEFAULT_SPEC_EMBEDDING_MAX_BATCH,
    DEFAULT_SPEC_LLM_MAX_TOKENS,
    DEFAULT_SPEC_LLM_TEMPERATURE,
    DEFAULT_SPEC_LLM_TOP_P,
    DEFAULT_SPEC_RERANK_BATCH_SIZE,
    DEFAULT_SPEC_RERANK_TOP_N,
)


class BindingPurpose(str, Enum):
    LLM_PRIMARY = "llm_primary"
    LLM_INTENT = "llm_intent"
    LLM_REWRITE = "llm_rewrite"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class LLMSpec(BaseModel):
    """Resolved LLM call spec (DB-driven)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    binding_id: UUID
    model_name: str  # provider-prefixed LiteLLM wire name
    provider: str
    temperature: float = DEFAULT_SPEC_LLM_TEMPERATURE
    max_tokens: int = DEFAULT_SPEC_LLM_MAX_TOKENS
    top_p: float = DEFAULT_SPEC_LLM_TOP_P
    fallback_chain: list[str] = Field(default_factory=list)  # LiteLLM model names
    cost_tier: Literal["cheap", "mid", "premium"] = "mid"
    variant: str | None = None  # for A/B
    # Mirrors ai_models.supports_vision — gates multimodal (image) message content.
    # Default False: a text-only spec; the VLM call site fails loud rather than send
    # an image to a non-vision model. Resolved from the DB flag by the model resolver.
    supports_vision: bool = False
    extra_params: dict[str, Any] = Field(default_factory=dict)

    def to_litellm_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }
        kwargs.update(self.extra_params)
        return kwargs


class PromptTemplate(BaseModel):
    """Resolved prompt template (per-bot, versioned in DB)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    template_key: str
    version: int
    jinja_source: str
    required_vars: frozenset[str]
    model_hint: str | None = None


class RerankerSpec(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    binding_id: UUID
    model_name: str
    provider: str
    top_n: int = DEFAULT_SPEC_RERANK_TOP_N
    batch_size: int = DEFAULT_SPEC_RERANK_BATCH_SIZE


class EmbeddingSpec(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    binding_id: UUID
    model_name: str
    provider: str
    dimension: int
    max_batch: int = DEFAULT_SPEC_EMBEDDING_MAX_BATCH
    model_version: str  # e.g. "bge-m3-v1"
    # Asymmetric-retrieval task selector for providers with separate query/passage heads.
    task: str | None = None


__all__ = ["EmbeddingSpec", "LLMSpec", "PromptTemplate", "RerankerSpec"]
