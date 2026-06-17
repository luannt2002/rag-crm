"""Vector store adapters — Strategy + DI."""

from ragbot.infrastructure.vector.null_vector_store import NullVectorStore
from ragbot.infrastructure.vector.pgvector_store import PgVectorStore
from ragbot.infrastructure.vector.registry import (
    build_vector_store,
    list_providers,
)

__all__ = [
    "NullVectorStore",
    "PgVectorStore",
    "build_vector_store",
    "list_providers",
]
