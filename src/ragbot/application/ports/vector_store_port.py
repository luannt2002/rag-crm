"""Vector store Protocol (pgvector impl in infrastructure).

Ref: PLAN_06 §vector_store_port.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ragbot.domain.entities.document import Chunk
from ragbot.shared.constants import DEFAULT_RAG_TOP_K
from ragbot.shared.types import (
    BotId,
    ChunkId,
    CorpusVersion,
    DocumentId,
    EmbeddingModelVersion,
    TenantId,
)


@dataclass(frozen=True, slots=True)
class HybridQuery:
    dense_vector: list[float]
    query_text: str
    sparse_vector: dict[int, float] | None = None  # SPLADE if available
    extra_filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    chunk_id: ChunkId
    document_id: DocumentId
    text: str
    score: float
    dense_score: float
    sparse_score: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStorePort(Protocol):
    async def health_check(self) -> bool: ...

    async def create_collection_if_not_exists(self) -> None: ...

    async def hybrid_search(
        self,
        query: HybridQuery,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        corpus_version: CorpusVersion,
        embedding_model_version: EmbeddingModelVersion,
        limit: int = DEFAULT_RAG_TOP_K,
    ) -> list[VectorCandidate]: ...

    async def upsert_chunks(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        *,
        record_tenant_id: TenantId,
    ) -> int: ...

    async def delete_by_document(
        self,
        document_id: DocumentId,
        *,
        record_tenant_id: TenantId,
    ) -> int: ...

    async def delete_by_tool_name(
        self,
        record_bot_id: BotId,
        tool_name: str,
        *,
        record_tenant_id: TenantId,
    ) -> int: ...

    async def close(self) -> None: ...


__all__ = ["HybridQuery", "VectorCandidate", "VectorStorePort"]
