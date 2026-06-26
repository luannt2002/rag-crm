"""DLC-1 / DLC-2 — worker wires BE-to-BE idempotency lifecycle.

The HTTP endpoint stamps an ``ingest_idempotency_keys`` row at state
``"processing"`` when the partner supplies ``X-Idempotency-Key``. Before
this wiring the worker never moved that row off ``"processing"`` — so a
follow-up retry could never short-circuit and the row leaked forever.

Contract pinned here:
  DLC-1: a SUCCESSFUL ingest calls ``mark_done`` with the document UUID;
         a TERMINAL failure (malformed payload) calls ``mark_failed`` so
         the row reaches a stable state — never stuck ``"processing"``.
  DLC-2: a TRANSIENT failure (429 / 5xx / connection) does NOT mark the
         row ``failed`` (the bus XCLAIM-retries it; the row's TTL is the
         backstop) AND the worker re-raises so the message is redelivered.
  No ``idempotency_key`` in the payload → idempotency service untouched
  (header-only opt-in, identical to the pre-wiring contract).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest


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

    idem = AsyncMock()
    container.ingest_idempotency_service.return_value = idem

    mock_uow = AsyncMock()
    mock_uow.add_outbox = AsyncMock()
    mock_uow.commit = AsyncMock()
    uow_factory = MagicMock()
    uow_factory.return_value.__aenter__ = AsyncMock(return_value=mock_uow)
    uow_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    container.uow_factory.return_value = uow_factory

    parsed_block = MagicMock(content="Hello world document content")
    parsed = MagicMock(blocks=[parsed_block], language="vi")
    ocr = AsyncMock()
    ocr.parse.return_value = parsed
    container.ocr.return_value = ocr
    return container


def _payload(*, idem_key: str | None = None) -> dict:
    p = {
        "record_tenant_id": str(uuid4()),
        "record_bot_id": str(uuid4()),
        "document_id": str(uuid4()),
        "job_id": str(uuid4()),
        "trace_id": "trace-123",
        "source_url": "https://example.com/doc.pdf",
        "tool_name": "test_doc",
        "mime_type": "application/pdf",
        "document_name": "Test Document",
        "workspace_id": "ws-1",
    }
    if idem_key is not None:
        p["idempotency_key"] = idem_key
    return p


def _patch_services():
    return (
        patch("ragbot.interfaces.workers.document_worker.DocumentService"),
        patch("ragbot.interfaces.workers.document_worker.SystemConfigService"),
    )


async def _run(container, payload):
    from ragbot.interfaces.workers.document_worker import handle_document_uploaded

    await handle_document_uploaded(payload, container)


@pytest.mark.asyncio
async def test_success_marks_idempotency_done(_mock_container):
    from ragbot.application.services.document_service import IngestResult

    p_doc, p_cfg = _patch_services()
    with p_doc as MockDoc, p_cfg:
        inst = AsyncMock()
        inst.ingest.return_value = IngestResult(
            document_id=uuid4(), title="t", chunks=3, embedded=True,
            chunks_new=3, chunks_unchanged=0, chunks_deleted=0,
        )
        MockDoc.return_value = inst
        payload = _payload(idem_key="abc-123")
        await _run(_mock_container, payload)

    idem = _mock_container.ingest_idempotency_service()
    idem.mark_done.assert_awaited_once()
    kwargs = idem.mark_done.call_args.kwargs
    assert kwargs["idempotency_key"] == "abc-123"
    assert str(kwargs["record_document_id"]) == payload["document_id"]
    idem.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_idem_key_leaves_service_untouched(_mock_container):
    from ragbot.application.services.document_service import IngestResult

    p_doc, p_cfg = _patch_services()
    with p_doc as MockDoc, p_cfg:
        inst = AsyncMock()
        inst.ingest.return_value = IngestResult(
            document_id=uuid4(), title="t", chunks=1, embedded=True,
            chunks_new=1, chunks_unchanged=0, chunks_deleted=0,
        )
        MockDoc.return_value = inst
        await _run(_mock_container, _payload(idem_key=None))

    idem = _mock_container.ingest_idempotency_service()
    idem.mark_done.assert_not_awaited()
    idem.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_failure_marks_idempotency_failed(_mock_container):
    """A terminal error (ValueError = malformed) → mark_failed, no re-raise."""
    p_doc, p_cfg = _patch_services()
    with p_doc as MockDoc, p_cfg:
        inst = AsyncMock()
        inst.ingest.side_effect = ValueError("malformed payload")
        MockDoc.return_value = inst
        # Must NOT raise (terminal errors are swallowed).
        await _run(_mock_container, _payload(idem_key="term-1"))

    idem = _mock_container.ingest_idempotency_service()
    idem.mark_failed.assert_awaited_once()
    assert idem.mark_failed.call_args.kwargs["idempotency_key"] == "term-1"
    idem.mark_done.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_failure_does_not_mark_failed_and_reraises(_mock_container):
    """A transient error (HTTP 5xx) leaves the row processing + re-raises
    so the bus redelivers; the TTL is the only backstop."""
    from ragbot.shared.errors import EmbeddingError

    p_doc, p_cfg = _patch_services()
    with p_doc as MockDoc, p_cfg:
        inst = AsyncMock()
        inst.ingest.side_effect = EmbeddingError("embed 503")
        MockDoc.return_value = inst
        with pytest.raises(EmbeddingError):
            await _run(_mock_container, _payload(idem_key="trans-1"))

    idem = _mock_container.ingest_idempotency_service()
    idem.mark_failed.assert_not_awaited()
    idem.mark_done.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotency_failure_never_breaks_ingest(_mock_container):
    """If the idempotency service itself errors, the worker must not crash
    the ingest success path (best-effort lifecycle marking)."""
    from ragbot.application.services.document_service import IngestResult

    idem = _mock_container.ingest_idempotency_service()
    idem.mark_done.side_effect = RuntimeError("idem db down")

    p_doc, p_cfg = _patch_services()
    with p_doc as MockDoc, p_cfg:
        inst = AsyncMock()
        inst.ingest.return_value = IngestResult(
            document_id=uuid4(), title="t", chunks=2, embedded=True,
            chunks_new=2, chunks_unchanged=0, chunks_deleted=0,
        )
        MockDoc.return_value = inst
        # Should not raise despite mark_done blowing up.
        await _run(_mock_container, _payload(idem_key="x"))

    job_repo = _mock_container.job_repo()
    any_success = any(
        c.kwargs.get("status") == "success"
        for c in job_repo.update_status.call_args_list
    )
    assert any_success, "ingest success must persist even when mark_done fails"
