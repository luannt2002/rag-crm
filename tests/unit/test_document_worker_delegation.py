"""Tests: document_worker delegates to DocumentService.ingest()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.fixture()
def _mock_container():
    """Build a minimal mock Container for document_worker tests."""
    container = MagicMock()
    container.job_repo.return_value = AsyncMock()
    container.settings.return_value = MagicMock(
        embedding=MagicMock(model_version="v1"),
    )
    container.clock.return_value = MagicMock(now=MagicMock(return_value="2026-01-01T00:00:00Z"))
    container.session_factory.return_value = MagicMock()
    container.redis_client.return_value = MagicMock()
    container.embedder.return_value = MagicMock()
    container.uow_factory.return_value = MagicMock(
        __call__=MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(),
            __aexit__=AsyncMock(),
            add_outbox=AsyncMock(),
            commit=AsyncMock(),
        )),
    )

    # OCR mock
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
async def test_worker_delegates_to_document_service(_mock_container, _sample_payload):
    """Worker must call DocumentService.ingest() instead of doing its own chunking."""
    from ragbot.application.services.document_service import IngestResult

    mock_ingest_result = IngestResult(
        document_id=uuid4(),
        title="Test Document",
        chunks=5,
        embedded=True,
        chunks_new=5,
        chunks_unchanged=0,
        chunks_deleted=0,
    )

    # Patch the UoW factory to return a proper async context manager
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
        mock_instance.ingest.return_value = mock_ingest_result
        MockDocService.return_value = mock_instance

        from ragbot.interfaces.workers.document_worker import handle_document_uploaded

        await handle_document_uploaded(_sample_payload, _mock_container)

        # Verify ingest was called
        mock_instance.ingest.assert_awaited_once()
        call_kwargs = mock_instance.ingest.call_args.kwargs
        assert call_kwargs["title"] == "Test Document"
        assert call_kwargs["source_url"] == "https://example.com/doc.pdf"


@pytest.mark.asyncio()
async def test_worker_publishes_failure_on_error(_mock_container, _sample_payload):
    """Worker must publish DocumentFailed event when ingest fails."""
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
        mock_instance.ingest.side_effect = RuntimeError("embedding service down")
        MockDocService.return_value = mock_instance

        from ragbot.interfaces.workers.document_worker import handle_document_uploaded

        # Should not raise — error handled internally
        await handle_document_uploaded(_sample_payload, _mock_container)

        # Job should be marked failed
        job_repo = _mock_container.job_repo()
        job_repo.update_status.assert_awaited()
        last_call = job_repo.update_status.call_args_list[-1]
        # Check that at least one update_status call had status="failed"
        all_calls = job_repo.update_status.call_args_list
        any_failed = any(c.kwargs.get("status") == "failed" for c in all_calls)
        assert any_failed, f"Expected status='failed' in update_status calls: {all_calls}"


@pytest.mark.asyncio()
async def test_worker_no_duplicate_chunking_logic():
    """Verify that document_worker does NOT contain its own chunking/embedding code."""
    import inspect
    from ragbot.interfaces.workers import document_worker

    source = inspect.getsource(document_worker)
    # Should NOT contain the old internal helpers
    assert "_naive_chunk" not in source, "Worker should not have _naive_chunk — delegate to DocumentService"
    assert "_recursive_chunk" not in source, "Worker should not have _recursive_chunk — delegate to DocumentService"
    assert "_add_contextual_prefix" not in source, "Worker should not have _add_contextual_prefix — delegate to DocumentService"
    assert "embed_batch" not in source, "Worker should not call embed_batch directly — delegate to DocumentService"
    # Should contain the delegation call
    assert "document_service" in source.lower() or "DocumentService" in source, "Worker must use DocumentService"
