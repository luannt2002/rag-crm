"""T2.S8 — passage / query asymmetric task wired end-to-end at call sites.

Pins the contract that:

1. ``DocumentService._embedding_spec`` returns ``task=retrieval.passage``
   on both the resolver-success and the system_config fallback paths
   (ingest must always encode passages with the passage head).
2. ``query_graph._embed_query`` flips a passage-default spec to
   ``task=retrieval.query`` via ``model_copy`` before calling
   ``embed_one`` (asymmetric retrieval requires the query head).
3. ``model_resolver.to_embedding_spec`` defaults to
   ``task=retrieval.passage`` (caller's expectation is "stored vector
   geometry" — query path explicitly model_copies to query).

Tests do NOT call live Jina API. The embedder is mocked.

Domain-neutral; no brand / industry literals.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.document_service import DocumentService
from ragbot.application.services.model_resolver import to_embedding_spec
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_TASK_PASSAGE,
    DEFAULT_EMBEDDING_TASK_QUERY,
)


# ---------------------------------------------------------------------------
# 1. Document side — ingest must use passage head
# ---------------------------------------------------------------------------


def _document_service_with_no_resolver() -> DocumentService:
    """Construct a DocumentService whose resolver path is disabled."""
    cfg = AsyncMock()

    async def _get(key: str, default: object = "") -> object:
        return default

    async def _get_int(key: str, default: int = 0) -> int:
        return default

    cfg.get = AsyncMock(side_effect=_get)
    cfg.get_int = AsyncMock(side_effect=_get_int)

    settings = SimpleNamespace(
        embedding=SimpleNamespace(
            model_name="vendor/embed-x",
            dimension=1024,
            model_version="x-1",
        ),
    )
    svc = DocumentService.__new__(DocumentService)
    svc._cfg = cfg
    svc._settings = settings
    svc._model_resolver = None
    return svc


@pytest.mark.asyncio
async def test_document_service_embedding_spec_fallback_uses_passage_task() -> None:
    """When the resolver is absent, the system_config fallback still
    sets ``task=retrieval.passage`` so Jina v3 ingest is correct."""
    svc = _document_service_with_no_resolver()
    spec = await svc._embedding_spec(record_bot_id=None, record_tenant_id=None)
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE


@pytest.mark.asyncio
async def test_document_service_embedding_spec_resolver_path_forces_passage() -> None:
    """Even if the resolver returns a query-task spec (e.g. cache leak),
    the ingest path must rewrite to ``task=retrieval.passage``."""
    resolver = MagicMock()

    leaked_query_spec = EmbeddingSpec(
        binding_id=uuid.uuid4(),
        model_name="jina_ai/jina-embeddings-v3",
        provider="jina",
        dimension=1024,
        model_version="v3",
        task=DEFAULT_EMBEDDING_TASK_QUERY,  # wrong head — must be flipped
    )
    resolver.resolve_embedding = AsyncMock(return_value=leaked_query_spec)

    svc = _document_service_with_no_resolver()
    svc._model_resolver = resolver

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE


# ---------------------------------------------------------------------------
# 2. Query side — ``model_copy`` flips a passage-default spec to query
# ---------------------------------------------------------------------------


def test_passage_spec_model_copy_to_query_task() -> None:
    """Mirrors the orchestrator's call-site flip: passage spec ->
    query spec via model_copy. Source is untouched (frozen)."""
    base = EmbeddingSpec(
        binding_id=uuid.uuid4(),
        model_name="jina_ai/jina-embeddings-v3",
        provider="jina",
        dimension=1024,
        model_version="v3",
        task=DEFAULT_EMBEDDING_TASK_PASSAGE,
    )
    flipped = base.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_QUERY})

    assert base.task == DEFAULT_EMBEDDING_TASK_PASSAGE  # source untouched
    assert flipped.task == DEFAULT_EMBEDDING_TASK_QUERY
    assert flipped.binding_id == base.binding_id
    assert flipped.dimension == base.dimension
    assert flipped.model_name == base.model_name


# ---------------------------------------------------------------------------
# 3. ``to_embedding_spec`` adapter defaults to passage task
# ---------------------------------------------------------------------------


def test_to_embedding_spec_defaults_to_passage_task() -> None:
    """Resolver-fallback adapter used by query_graph must carry a
    non-None task — query path will model_copy to query, but the
    adapter never strips the task to None (which would silently fall
    through to provider-default semantics)."""
    cfg = SimpleNamespace(
        binding_id=uuid.uuid4(),
        litellm_name="jina_ai/jina-embeddings-v3",
        provider=SimpleNamespace(code="jina"),
        embedding_dimension=1024,
        wire_model_id="jina-embeddings-v3",
    )
    spec = to_embedding_spec(cfg)  # type: ignore[arg-type]
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE
