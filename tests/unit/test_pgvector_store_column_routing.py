"""PgVectorStore single embedding column contract.

The data table has a single ``embedding vector(N)`` column;
``embedding_column`` kwarg is preserved for the SQL-injection defence.

Locks:
1. ``search`` / ``hybrid_search`` / ``upsert_chunks`` accept the
   ``embedding_column`` kwarg with default ``DEFAULT_EMBEDDING_COLUMN``.
2. The whitelist rejects unknown / SQL-injection-y column names.

Domain-neutral. No brand / industry literals.
"""

from __future__ import annotations

import inspect

import pytest

from ragbot.infrastructure.vector.pgvector_store import (
    PgVectorStore,
    _validate_embedding_column,
)
from ragbot.shared.constants import DEFAULT_EMBEDDING_COLUMN


def test_search_signature_accepts_embedding_column_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.search)
    assert "embedding_column" in sig.parameters
    assert sig.parameters["embedding_column"].default == DEFAULT_EMBEDDING_COLUMN


def test_hybrid_search_signature_accepts_embedding_column_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.hybrid_search)
    assert "embedding_column" in sig.parameters
    assert sig.parameters["embedding_column"].default == DEFAULT_EMBEDDING_COLUMN


def test_upsert_chunks_signature_accepts_embedding_column_kwarg() -> None:
    sig = inspect.signature(PgVectorStore.upsert_chunks)
    assert "embedding_column" in sig.parameters
    assert sig.parameters["embedding_column"].default == DEFAULT_EMBEDDING_COLUMN


def test_validator_accepts_default_column() -> None:
    assert (
        _validate_embedding_column(DEFAULT_EMBEDDING_COLUMN)
        == DEFAULT_EMBEDDING_COLUMN
    )


def test_validator_rejects_unknown_column() -> None:
    with pytest.raises(ValueError, match="unsupported embedding column"):
        _validate_embedding_column("not_a_real_embedding_col")


def test_validator_rejects_sql_injection_attempt() -> None:
    with pytest.raises(ValueError, match="unsupported embedding column"):
        _validate_embedding_column("embedding; DROP TABLE document_chunks;--")


def test_constants_match_db_schema_name() -> None:
    """The data table has a single column named ``embedding``."""
    assert DEFAULT_EMBEDDING_COLUMN == "embedding"
