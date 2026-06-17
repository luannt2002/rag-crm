"""Strategy ports — DB-driven AI selection.

These ports decouple "what model to use" from "how to call the LLM".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ragbot.application.dto.ai_specs import (
    EmbeddingSpec,
    LLMSpec,
    PromptTemplate,
    RerankerSpec,
)
from ragbot.domain.entities.document_profile import DocumentProfile
from ragbot.shared.types import (
    BotId,
    ChunkingStrategyName,
    ConversationId,
    LLMIntent,
    TenantId,
)


@dataclass(frozen=True, slots=True)
class ChunkingDecision:
    strategy: ChunkingStrategyName
    forced: bool  # True if locked by config; False if LLM-selected
    confidence: float
    reasoning: str = ""


@runtime_checkable
class ModelSelectionStrategyPort(Protocol):
    async def select_llm(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        intent: LLMIntent,
        iter: int = 0,
        conversation_id: ConversationId | None = None,
    ) -> LLMSpec: ...

    async def select_fallback_chain(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        intent: LLMIntent,
    ) -> list[LLMSpec]: ...


@runtime_checkable
class PromptStrategyPort(Protocol):
    async def load_template(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        template_key: str,
        version: int | None = None,
    ) -> PromptTemplate: ...

    async def list_versions(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        template_key: str,
    ) -> list[int]: ...


@runtime_checkable
class ChunkingStrategyResolverPort(Protocol):
    async def resolve_strategy(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        document_profile: DocumentProfile,
    ) -> ChunkingDecision: ...


@runtime_checkable
class RerankerStrategyPort(Protocol):
    async def select_reranker(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
    ) -> RerankerSpec: ...


@runtime_checkable
class EmbeddingStrategyPort(Protocol):
    async def select_embedding(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
    ) -> EmbeddingSpec: ...


__all__ = [
    "ChunkingDecision",
    "ChunkingStrategyResolverPort",
    "EmbeddingStrategyPort",
    "ModelSelectionStrategyPort",
    "PromptStrategyPort",
    "RerankerStrategyPort",
]
