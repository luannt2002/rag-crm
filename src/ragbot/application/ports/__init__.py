"""Application ports — Protocol interfaces; adapters live in ``ragbot.infrastructure``."""

from ragbot.application.ports.ai_config_port import AIConfigRepositoryPort, BindingRow
from ragbot.application.ports.bus_port import EventBusPort, SubscriptionHandle
from ragbot.application.ports.cache_port import (
    CachePort,
    CachedResponse,
    SemanticCachePort,
    build_chunks_cache_key,
    build_embedding_cache_key,
    build_response_cache_key,
    build_semantic_cache_key,
)
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.application.ports.guardrail_port import GuardrailPort, ModerationOutcome
from ragbot.application.ports.language_pack_repository_port import (
    LanguagePackRepositoryPort,
)
from ragbot.application.ports.llm_port import LLMMessage, LLMPort, LLMResponse
from ragbot.application.ports.metrics_port import MetricsPort
from ragbot.application.ports.ocr_port import OCRPort, ParsedDocument
from ragbot.application.ports.outbox_port import OutboxRecord, OutboxRepositoryPort
from ragbot.application.ports.pii_port import PIIRedactorPort
from ragbot.application.ports.repository_ports import (
    BotRepositoryPort,
    ConversationRepositoryPort,
    DocumentRepositoryPort,
    JobRepositoryPort,
    QuotaRepositoryPort,
    UnitOfWorkPort,
)
from ragbot.application.ports.reranker_port import RerankerPort
from ragbot.application.ports.strategy_ports import (
    ChunkingDecision,
    ChunkingStrategyResolverPort,
    ModelSelectionStrategyPort,
    PromptStrategyPort,
    RerankerStrategyPort,
)
from ragbot.application.ports.vector_store_port import (
    HybridQuery,
    VectorCandidate,
    VectorStorePort,
)

__all__ = [
    "AIConfigRepositoryPort",
    "BindingRow",
    "BotRepositoryPort",
    "CachePort",
    "CachedResponse",
    "ChunkingDecision",
    "ChunkingStrategyResolverPort",
    "ConversationRepositoryPort",
    "DocumentRepositoryPort",
    "EmbeddingPort",
    "EventBusPort",
    "GuardrailPort",
    "HybridQuery",
    "JobRepositoryPort",
    "LanguagePackRepositoryPort",
    "LLMMessage",
    "LLMPort",
    "LLMResponse",
    "MetricsPort",
    "ModelSelectionStrategyPort",
    "ModerationOutcome",
    "OCRPort",
    "OutboxRecord",
    "OutboxRepositoryPort",
    "PIIRedactorPort",
    "ParsedDocument",
    "PromptStrategyPort",
    "QuotaRepositoryPort",
    "RerankerPort",
    "RerankerStrategyPort",
    "SemanticCachePort",
    "SubscriptionHandle",
    "UnitOfWorkPort",
    "VectorCandidate",
    "VectorStorePort",
    "build_chunks_cache_key",
    "build_embedding_cache_key",
    "build_response_cache_key",
    "build_semantic_cache_key",
]
