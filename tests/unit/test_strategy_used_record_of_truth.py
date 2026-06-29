"""F4: DocumentIngested.strategy_used is a record-of-truth, not a hardcoded literal.

Three behavioural guarantees:
  1. The ingest pipeline surfaces the REAL resolved strategy on IngestResult
     (U4 ctx.strategy_used -> IngestResult.strategy_used).
  2. The worker publishes the DocumentIngested event using
     ``result.strategy_used`` -- NOT the old hardcoded ``\"SEMANTIC\"``.
  3. The ChunkingStrategyName type is reconciled to admit the real runtime
     strategy names so surfacing them is type-faithful (no lossy bucketing).
"""
from __future__ import annotations

from typing import get_args
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# 1. IngestResult carries strategy_used (default from constants, overridable)
# ---------------------------------------------------------------------------
def test_ingest_result_carries_strategy_used_default_from_constants() -> None:
    from ragbot.application.services.document_service import IngestResult
    from ragbot.shared.constants import DEFAULT_INGEST_STRATEGY_NAME

    # Default = baseline constant (zero-hardcode), NOT the old "SEMANTIC" literal.
    res = IngestResult(document_id=uuid4(), title="t", chunks=3, embedded=True)
    assert res.strategy_used == DEFAULT_INGEST_STRATEGY_NAME
    assert res.strategy_used != "SEMANTIC"

    # Real resolved runtime names flow through unchanged (record-of-truth).
    for name in ("hdt", "whole_document", "parent_child", "parser_preserve"):
        r = IngestResult(
            document_id=uuid4(), title="t", chunks=1, embedded=True,
            strategy_used=name,
        )
        assert r.strategy_used == name


# ---------------------------------------------------------------------------
# 2. _IngestCtx -> IngestResult threads the U4 reconciled strategy
# ---------------------------------------------------------------------------
def test_ctx_strategy_used_field_exists_with_baseline_default() -> None:
    from ragbot.application.services.document_service.ingest_stages import (
        _IngestCtx,
    )

    ctx = _IngestCtx(
        record_bot_id=uuid4(),
        title="t",
        content="hello",
        source_url="",
        source_type="worker",
        language="vi",
        mime_type="text/plain",
        existing_doc_id=None,
        record_tenant_id=uuid4(),
        workspace_id="system",
        channel_type="web",
        raw_bytes=None,
        file_name=None,
        blocks=None,
        step_tracker=None,
    )
    # Baseline default matches the auto-detect baseline name.
    assert ctx.strategy_used == "recursive"
    # Stage code can reconcile it to a special-branch name; it must persist.
    ctx.strategy_used = "whole_document"
    assert ctx.strategy_used == "whole_document"


# ---------------------------------------------------------------------------
# 3. ChunkingStrategyName reconciled to admit the real runtime names
# ---------------------------------------------------------------------------
def test_chunking_strategy_name_admits_runtime_and_taxonomy() -> None:
    from ragbot.shared.types import ChunkingStrategyName

    members = set(get_args(ChunkingStrategyName))
    # Existing AdapChunk taxonomy retained (backward compatible).
    assert {"HDT", "SEMANTIC", "PROPOSITION", "HYBRID"} <= members
    # Real runtime selector + special-branch names now representable.
    assert {
        "recursive", "semantic", "hdt", "hybrid", "proposition",
        "whole_document", "parent_child", "parser_preserve",
        "table_csv", "table_dual_index",
    } <= members


# ---------------------------------------------------------------------------
# 4. Worker publishes the event with result.strategy_used (NOT "SEMANTIC")
# ---------------------------------------------------------------------------
@pytest.fixture()
def _mock_container():
    container = MagicMock()
    container.job_repo.return_value = AsyncMock()
    container.settings.return_value = MagicMock(
        embedding=MagicMock(model_version="v1"),
    )
    container.clock.return_value = MagicMock(
        now=MagicMock(return_value="2026-01-01T00:00:00Z"),
    )
    container.session_factory.return_value = MagicMock()
    container.redis_client.return_value = MagicMock()
    container.embedder.return_value = MagicMock()

    parsed_block = MagicMock(content="Hello world document content")
    parsed = MagicMock(blocks=[parsed_block], language="vi")
    ocr = AsyncMock()
    ocr.parse.return_value = parsed
    container.ocr.return_value = ocr
    return container


@pytest.fixture()
def _sample_payload():
    return {
        "record_tenant_id": str(uuid4()),
        "record_bot_id": str(uuid4()),
        "document_id": str(uuid4()),
        "job_id": str(uuid4()),
        "trace_id": "trace-123",
        "source_url": "https://example.com/doc.pdf",
        "tool_name": "test_doc",
        "mime_type": "application/pdf",
        "document_name": "Test Document",
    }


@pytest.mark.asyncio()
async def test_worker_event_uses_resolved_strategy_not_literal(
    _mock_container, _sample_payload,
):
    from ragbot.application.services.document_service import IngestResult

    # Ingest resolves a NON-default, NON-"SEMANTIC" strategy -> the event MUST
    # carry exactly this, proving the worker reads result.strategy_used.
    resolved = "whole_document"
    mock_result = IngestResult(
        document_id=uuid4(),
        title="Test Document",
        chunks=5,
        embedded=True,
        chunks_new=5,
        strategy_used=resolved,
    )

    mock_uow = AsyncMock()
    mock_uow.add_outbox = AsyncMock()
    mock_uow.commit = AsyncMock()
    mock_uow_factory = MagicMock()
    mock_uow_factory.return_value.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    _mock_container.uow_factory.return_value = mock_uow_factory

    with (
        patch(
            "ragbot.interfaces.workers.document_worker.DocumentService",
        ) as MockDocService,
        patch(
            "ragbot.interfaces.workers.document_worker.SystemConfigService",
        ),
    ):
        mock_instance = AsyncMock()
        mock_instance.ingest.return_value = mock_result
        MockDocService.return_value = mock_instance

        from ragbot.interfaces.workers.document_worker import (
            handle_document_uploaded,
        )

        await handle_document_uploaded(_sample_payload, _mock_container)

    mock_uow.add_outbox.assert_awaited()
    # The DocumentIngested event is the single positional arg to add_outbox.
    published = mock_uow.add_outbox.call_args.args[0]
    assert getattr(published, "strategy_used") == resolved
    assert getattr(published, "strategy_used") != "SEMANTIC"
