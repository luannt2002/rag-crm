"""DocumentService.ingest resolves embedding per-bot.

The ingest path must read ``bot_model_bindings`` (per-bot) ahead of
``system_config`` (global). Without this chain, bots whose binding
declares a different dimension would silently inherit the wrong vector
space at ingest, and the chunk column would mismatch the query path's
vector space. These tests pin the resolver-first chain:

  ModelResolverService.resolve_embedding(record_bot_id, record_tenant_id)
  > system_config.embedding_*
  > Settings.embedding.*

Resolver failures (no binding, repo error) MUST fall back gracefully so
legacy bots keep working until they're migrated.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.document_service import DocumentService
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_TASK_PASSAGE,
)


def _make_settings(
    *,
    model_name: str = "text-embedding-3-small",
    dimension: int = 1536,
    model_version: str = "text-embedding-3-small-v1",
) -> MagicMock:
    s = MagicMock()
    s.embedding.model_name = model_name
    s.embedding.dimension = dimension
    s.embedding.model_version = model_version
    return s


def _make_session_factory() -> MagicMock:
    """Mock session_factory(); not exercised in _embedding_spec — safe stub."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    @asynccontextmanager
    async def _cm():
        yield mock_session

    sf = MagicMock(side_effect=lambda: _cm())
    return sf


def _make_service(
    *,
    model_resolver: object | None,
    config_service: object | None = None,
    settings: MagicMock | None = None,
) -> DocumentService:
    return DocumentService(
        session_factory=_make_session_factory(),
        embedder=MagicMock(),
        settings=settings or _make_settings(),
        config_service=config_service,
        model_resolver=model_resolver,
    )


def _jina_1024d_spec() -> EmbeddingSpec:
    """Canonical 1024-d spec returned by the per-bot resolver."""
    return EmbeddingSpec(
        binding_id=uuid.uuid4(),
        model_name="jina_ai/jina-embeddings-v3",
        provider="jina_ai",
        dimension=1024,
        max_batch=DEFAULT_EMBEDDING_MAX_BATCH,
        model_version="jina-embeddings-v3",
        task=DEFAULT_EMBEDDING_TASK_PASSAGE,
    )


@pytest.mark.asyncio
async def test_embedding_spec_uses_resolver_when_record_bot_id_provided() -> None:
    """Resolver returns 1024-d spec → ingest spec must be 1024-d (NOT 1536)."""
    resolver = MagicMock()
    resolver.resolve_embedding = AsyncMock(return_value=_jina_1024d_spec())

    cfg = MagicMock()
    # Sentinel: if these get called, the resolver-first contract is broken.
    cfg.get = AsyncMock(return_value="text-embedding-3-small")
    cfg.get_int = AsyncMock(return_value=1536)

    svc = _make_service(model_resolver=resolver, config_service=cfg)

    bot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    spec = await svc._embedding_spec(
        record_bot_id=bot_id,
        record_tenant_id=tenant_id,
    )

    assert spec.dimension == 1024, "Resolver path must override system_config dim."
    assert spec.provider == "jina_ai"
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE
    resolver.resolve_embedding.assert_awaited_once_with(
        bot_id, record_tenant_id=tenant_id,
    )
    # system_config must NOT be consulted when resolver succeeds.
    cfg.get.assert_not_awaited()
    cfg.get_int.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_spec_falls_back_to_system_config_when_resolver_none() -> None:
    """No resolver injected → legacy system_config path."""
    cfg = MagicMock()
    cfg.get = AsyncMock(side_effect=["text-embedding-3-small", "v1"])
    cfg.get_int = AsyncMock(return_value=1536)

    svc = _make_service(model_resolver=None, config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )

    assert spec.dimension == 1536
    assert spec.model_name == "text-embedding-3-small"
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE
    cfg.get_int.assert_awaited()


@pytest.mark.asyncio
async def test_embedding_spec_falls_back_to_system_config_when_no_binding() -> None:
    """Resolver raises (e.g. no per-bot binding) → fall back, do not crash."""
    from ragbot.shared.errors import InvariantViolation

    resolver = MagicMock()
    resolver.resolve_embedding = AsyncMock(
        side_effect=InvariantViolation("No embedding binding for bot ..."),
    )

    cfg = MagicMock()
    cfg.get = AsyncMock(side_effect=["text-embedding-3-small", "v1"])
    cfg.get_int = AsyncMock(return_value=1536)

    svc = _make_service(model_resolver=resolver, config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )

    assert spec.dimension == 1536, "Fallback must yield platform default dim."
    assert spec.model_name == "text-embedding-3-small"
    cfg.get_int.assert_awaited()


@pytest.mark.asyncio
async def test_embedding_spec_skips_resolver_when_ids_missing() -> None:
    """No record_bot_id / record_tenant_id → resolver bypassed (legacy callers)."""
    resolver = MagicMock()
    resolver.resolve_embedding = AsyncMock(return_value=_jina_1024d_spec())

    cfg = MagicMock()
    cfg.get = AsyncMock(side_effect=["text-embedding-3-small", "v1"])
    cfg.get_int = AsyncMock(return_value=1536)

    svc = _make_service(model_resolver=resolver, config_service=cfg)

    spec = await svc._embedding_spec()  # no kwargs → fallback chain

    assert spec.dimension == 1536
    resolver.resolve_embedding.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_spec_forces_passage_task_on_resolver_result() -> None:
    """Resolver may default to passage; ingest path must guarantee passage head.

    Some embedders are symmetric and ignore ``task``, but asymmetric
    embedding models require the passage head at ingest time.
    """
    resolver = MagicMock()
    # Simulate a resolver that returned a query-task spec by mistake.
    bad_spec = _jina_1024d_spec().model_copy(update={"task": "retrieval.query"})
    resolver.resolve_embedding = AsyncMock(return_value=bad_spec)

    svc = _make_service(model_resolver=resolver)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )

    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE, (
        "Ingest path must always encode passages with the passage head."
    )
    # Other fields preserved.
    assert spec.dimension == 1024
    assert spec.provider == "jina_ai"


def test_constructor_accepts_model_resolver_kwarg() -> None:
    """Constructor signature pin — DI wiring depends on this kwarg name."""
    import inspect

    sig = inspect.signature(DocumentService.__init__)
    assert "model_resolver" in sig.parameters, (
        "DocumentService must accept ``model_resolver`` for per-bot "
        "embedding binding resolution at ingest time."
    )
    # Optional / defaults to None for backward-compat.
    assert sig.parameters["model_resolver"].default is None
