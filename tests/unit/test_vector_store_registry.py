"""Vector store registry — Strategy + DI factory unit tests.

Pins (Agent R Task R.1 — Vector store factory):

- Registry resolves ``pgvector`` / ``postgres`` aliases to ``PgVectorStore``.
- Registry resolves ``null`` to ``NullVectorStore`` (no-op fail-safe).
- Unknown / typo / empty / None provider → ``NullVectorStore`` (fail-soft).
- ``list_providers()`` returns sorted, stable key list.
- ``build_vector_store`` filters kwargs to constructor signature so a globally-
  passed kwarg (e.g. ``dimension=`` for PgVectorStore) does not blow up
  NullVectorStore which takes no kwargs.
- Domain-neutral — no brand / industry literal.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ragbot.infrastructure.vector.null_vector_store import NullVectorStore
from ragbot.infrastructure.vector.pgvector_store import PgVectorStore
from ragbot.infrastructure.vector.registry import (
    build_vector_store,
    list_providers,
)


# --------------------------------------------------------------------------- #
# Registry resolution                                                         #
# --------------------------------------------------------------------------- #


def test_registry_resolves_pgvector_to_pgvector_store() -> None:
    sf = MagicMock()
    store = build_vector_store("pgvector", session_factory=sf)
    assert isinstance(store, PgVectorStore)


def test_registry_resolves_postgres_alias_to_pgvector_store() -> None:
    """`postgres` is a friendly alias matching ai_providers.code convention."""
    sf = MagicMock()
    store = build_vector_store("postgres", session_factory=sf)
    assert isinstance(store, PgVectorStore)


def test_registry_resolves_null_to_null_vector_store() -> None:
    store = build_vector_store("null")
    assert isinstance(store, NullVectorStore)


def test_registry_unknown_provider_fails_soft_to_null() -> None:
    """Typo / unknown key collapses to NullVectorStore — no boot crash."""
    for prov in ("does_not_exist_xyz", "qdrant_typo", "weaviate"):
        store = build_vector_store(prov)
        assert isinstance(store, NullVectorStore), f"prov={prov!r}"


def test_registry_falsy_provider_falls_back_to_null() -> None:
    """None / "" / "   " all collapse to NullVectorStore."""
    for prov in (None, "", "   "):
        store = build_vector_store(prov)
        assert isinstance(store, NullVectorStore), f"prov={prov!r}"


def test_registry_case_insensitive_resolution() -> None:
    sf = MagicMock()
    assert isinstance(build_vector_store("PGVECTOR", session_factory=sf), PgVectorStore)
    assert isinstance(build_vector_store("Null"), NullVectorStore)


def test_list_providers_sorted_and_complete() -> None:
    providers = list_providers()
    assert "pgvector" in providers
    assert "postgres" in providers
    assert "null" in providers
    assert providers == sorted(providers), "list_providers must return sorted"


# --------------------------------------------------------------------------- #
# Kwargs filtering — Open-Closed compatibility                                #
# --------------------------------------------------------------------------- #


def test_kwargs_filtered_to_constructor_signature() -> None:
    """Unknown kwargs are filtered out by inspect.signature, so a globally-
    passed dimension= does not crash NullVectorStore (which accepts **_).
    """
    sf = MagicMock()
    # Both kwargs accepted by Pg ctor.
    store = build_vector_store("pgvector", session_factory=sf, dimension=1280)
    assert isinstance(store, PgVectorStore)
    # NullVectorStore takes **_ — anything is fine.
    null = build_vector_store("null", session_factory=sf, dimension=1280, foo="bar")
    assert isinstance(null, NullVectorStore)


# --------------------------------------------------------------------------- #
# NullVectorStore behaviour                                                   #
# --------------------------------------------------------------------------- #


def test_null_vector_store_methods_return_safe_defaults() -> None:
    """No-op contract: upsert → 0, delete → 0, search → [], count → 0, health → True."""
    import asyncio
    from uuid import uuid4

    store = NullVectorStore()
    # All methods must be awaitable and return safe defaults.
    assert asyncio.run(store.upsert_chunks(record_document_id=uuid4(), chunks=[])) == 0
    assert asyncio.run(store.delete_by_document(uuid4())) == 0
    assert asyncio.run(
        store.search(query_embedding=[0.0], record_bot_id=uuid4()),
    ) == []
    assert asyncio.run(
        store.hybrid_search(
            query_text="q", query_embedding=[0.0], record_bot_id=uuid4(),
        ),
    ) == []
    assert asyncio.run(store.count(uuid4())) == 0
    assert asyncio.run(store.health_check()) is True
    # close() must be awaitable and return None without raising.
    assert asyncio.run(store.close()) is None


def test_null_vector_store_tolerates_any_kwargs() -> None:
    """Constructor accepts arbitrary kwargs (mirrors registry kwargs filter)."""
    store = NullVectorStore(session_factory="anything", dimension=9999, foo="bar")
    assert isinstance(store, NullVectorStore)


def test_concrete_stores_implement_every_port_method() -> None:
    """Regression 2026-06-20: PgVectorStore was MISSING ``delete_by_tool_name``
    (declared on ``VectorStorePort``) → the canonical ``DELETE /documents``
    use-case raised ``AttributeError`` → HTTP 500, and the follow-up re-create
    then hit a UniqueViolation. Pin that BOTH concrete stores implement every
    public Protocol method so a port/impl drift can't 500 the canonical ingest
    path again.
    """
    # The methods the pipeline / use-cases actually CALL — these MUST exist on
    # every concrete store (the declared-but-uncalled port methods like
    # ``health_check`` are harmless port-bloat and intentionally not asserted).
    called_methods = (
        "upsert_chunks", "delete_by_document", "delete_by_tool_name",
        "hybrid_search",
    )
    for impl in (PgVectorStore, NullVectorStore):
        missing = [m for m in called_methods if not hasattr(impl, m)]
        assert not missing, f"{impl.__name__} missing called port methods: {missing}"
