"""NullVectorStore — Null Object pattern for the vector store strategy.

Default-OFF fail-safe used when the operator has not configured a real
vector backend (or when an unknown provider key flips the registry to its
fail-soft path). Mirrors :class:`PgVectorStore`'s method signatures so the
DI container can swap implementations without orchestrator edits.

Selecting ``NullVectorStore`` via ``system_config.vector_store_provider =
"null"`` is a **deliberate operator choice**: retrieval returns empty, the
ingest path no-ops (writes succeed at 0 rows), and the rest of the pipeline
keeps running so the misconfig stays observable in logs without crashing
the request flow.

All methods accept ``**_: Any`` so a globally-passed kwarg (e.g.
``dimension=1280``, ``embedding_column="embedding"``) from the DI factory
does not blow up the constructor / call site — symmetric with the
NullReranker / NullFilter pattern.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class NullVectorStore:
    """No-op vector store — upserts succeed at zero rows, searches return [].

    Used as the registry fail-soft target and as the explicit
    operator-disabled default. Keeps the rest of the chat / ingest pipeline
    running so the misconfig is loud in logs without taking down the request.
    """

    def __init__(self, **_: Any) -> None:
        # Accept any kwargs from the DI factory (session_factory=, dimension=,
        # …). Stored nowhere — the null implementation has no state.
        logger.debug("null_vector_store_initialized")

    async def upsert_chunks(
        self,
        *,
        record_document_id: UUID,
        chunks: list[dict[str, Any]],
        **_: Any,
    ) -> int:
        """No-op ingest: log + return 0 rows written."""
        logger.debug(
            "null_vector_store_upsert_bypass",
            record_document_id=str(record_document_id),
            chunks_in=len(chunks or []),
        )
        return 0

    async def delete_by_document(
        self,
        record_document_id: UUID,
        **_: Any,
    ) -> int:
        """No-op delete: log + return 0 rows deleted."""
        logger.debug(
            "null_vector_store_delete_bypass",
            record_document_id=str(record_document_id),
        )
        return 0

    async def search(
        self,
        *,
        query_embedding: list[float],
        record_bot_id: UUID,
        **_: Any,
    ) -> list[dict[str, Any]]:
        """No-op vector search: log + return []. Retrieval continues empty."""
        logger.debug(
            "null_vector_store_search_bypass",
            record_bot_id=str(record_bot_id),
            embedding_len=len(query_embedding or []),
        )
        return []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id: UUID,
        **_: Any,
    ) -> list[dict[str, Any]]:
        """No-op hybrid search: log + return []."""
        logger.debug(
            "null_vector_store_hybrid_bypass",
            record_bot_id=str(record_bot_id),
            query_len=len(query_text or ""),
        )
        return []

    async def count(self, record_bot_id: UUID, **_: Any) -> int:
        """No-op count: return 0 chunks for any bot."""
        return 0

    async def health_check(self) -> bool:
        """Always healthy — the null implementation has no external dependency."""
        return True

    async def close(self) -> None:  # pragma: no cover - trivial
        return None


__all__ = ["NullVectorStore"]
