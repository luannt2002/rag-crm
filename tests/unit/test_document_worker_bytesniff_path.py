"""P0-5 pin — the document worker routes URL/type-detection through the
CANONICAL byte-sniff path (``detect_parser_robust``), not plain
``detect_parser``.

Before P0-5 the worker funnel
(``_handle_document_uploaded_inner``) called the non-robust
``detect_parser(mime, ext)`` BEFORE fetching the body, so a refetchable URL
whose body arrives as ``application/octet-stream`` with no extension (e.g. a
``?download`` PDF/XLSX link) returned ``None`` and the document silently
dropped to flat OCR — a SECOND, divergent detection path from
``DocumentService`` (which already byte-sniffs via ``detect_parser_robust``).

After P0-5 the worker fetches the body first, then calls
``detect_parser_robust(mime, ext, raw, detector=detect_parser)`` — the exact
order DocumentService uses. These tests assert the WORKER itself takes that
path (spying the function the worker imports), not merely that the registry
function exists.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _xlsx_bytes() -> bytes:
    """Minimal OOXML zip whose ``[Content_Types].xml`` names the spreadsheet
    type — the exact shape ``_sniff_mime`` resolves to a structured parser."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types>'
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            "</Types>",
        )
    return buf.getvalue()


def _mock_httpx_returning(body: bytes):
    """Patch object for ``document_worker.httpx`` whose AsyncClient streams
    *body* with a successful status. The worker fetches via the bounded
    ``_fetch_url_bounded`` helper (``cli.stream(...)`` async CM + ``aiter_bytes``),
    so the mock must model streaming, not a plain ``get``."""
    resp = MagicMock()
    resp.content = body
    resp.headers = {}  # no Content-Length → exercise the streaming-guard path
    resp.raise_for_status = MagicMock(return_value=None)

    async def _aiter(_size: int | None = None):
        yield body

    resp.aiter_bytes = _aiter

    class _StreamCtx:
        async def __aenter__(self) -> object:
            return resp

        async def __aexit__(self, *_a: object) -> bool:
            return False

    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)  # retained for any legacy caller
    # ``stream`` returns the async CM SYNCHRONOUSLY (like real httpx) — override
    # the AsyncMock attribute so it is not a coroutine.
    client.stream = MagicMock(return_value=_StreamCtx())
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    httpx_mod = MagicMock()
    httpx_mod.AsyncClient = MagicMock(return_value=client)
    # Keep the real exception hierarchy so the worker's transient-error
    # classification still type-checks against httpx.HTTPError.
    import httpx as _real_httpx

    httpx_mod.HTTPError = _real_httpx.HTTPError
    return httpx_mod


def _container_for_fetch_branch() -> MagicMock:
    """Minimal container that drives the worker into the refetchable-URL fetch
    branch (no stored raw_content reuse — that path is local:// only)."""
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
    container.model_resolver.return_value = AsyncMock()
    container.llm.return_value = MagicMock()
    container.bot_repo.return_value = AsyncMock()
    # OCR mock — only reached if the structured parser path fails; present so a
    # fallthrough never crashes the test.
    ocr_parsed = MagicMock(blocks=[MagicMock(content="x")], language="vi")
    ocr = AsyncMock()
    ocr.parse.return_value = ocr_parsed
    container.ocr.return_value = ocr
    return container


def _payload(mime_type: str) -> dict:
    return {
        "record_tenant_id": str(uuid4()),
        "record_bot_id": str(uuid4()),
        "document_id": str(uuid4()),
        "job_id": str(uuid4()),
        "trace_id": "trace-bytesniff",
        "source_url": "https://example.com/data?download=1",
        "tool_name": "test_doc",
        "mime_type": mime_type,
        "document_name": "sheet",  # no extension -> ext detection is empty
    }


@pytest.mark.asyncio()
async def test_worker_octet_stream_url_routed_via_byte_sniff() -> None:
    """An octet-stream URL with no ext routes through the worker's
    ``detect_parser_robust`` call, which receives the FETCHED bytes — proving
    the worker uses the canonical byte-sniff path, not plain detect_parser."""
    from ragbot.interfaces.workers import document_worker

    xlsx = _xlsx_bytes()
    container = _container_for_fetch_branch()

    # Fake structured parser the robust-detect returns once it sniffs the body.
    fake_parser = AsyncMock()
    fake_parser.parse = AsyncMock(
        return_value=[{"content": "| a | b |\n| 1 | 2 |", "metadata": {}}],
    )
    fake_parser.get_provider_name = MagicMock(return_value="excel_openpyxl")

    robust_spy = MagicMock(return_value=fake_parser)

    ingest_result = MagicMock(
        document_id=uuid4(), chunks=1, strategy_used="structural",
    )
    doc_service = AsyncMock()
    doc_service.ingest = AsyncMock(return_value=ingest_result)

    uow = AsyncMock()
    uow.add_outbox = AsyncMock()
    uow.commit = AsyncMock()
    uow_factory = MagicMock()
    uow_factory.return_value.__aenter__ = AsyncMock(return_value=uow)
    uow_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    container.uow_factory.return_value = uow_factory

    with (
        patch.object(document_worker, "httpx", _mock_httpx_returning(xlsx)),
        patch.object(document_worker, "detect_parser_robust", robust_spy),
        patch.object(
            document_worker, "DocumentService", MagicMock(return_value=doc_service),
        ),
        patch.object(document_worker, "SystemConfigService"),
        patch.object(
            document_worker, "_try_build_vlm_image_parser",
            AsyncMock(return_value=None),
        ),
        patch.object(document_worker, "NarrateService", MagicMock()),
        patch.object(document_worker, "build_narrate", MagicMock()),
        patch.object(document_worker, "ChunkContextEnricher", MagicMock()),
        patch.object(document_worker, "LLMChunkContextProvider", MagicMock()),
    ):
        await document_worker.handle_document_uploaded(
            _payload("application/octet-stream"), container,
        )

    # The worker MUST have consulted the byte-sniff detector...
    assert robust_spy.called, (
        "worker did not route detection through detect_parser_robust — "
        "it is still on the non-robust plain detect_parser path"
    )
    # ...with the ACTUAL fetched bytes (3rd positional arg = content), proving
    # the fetch happens before detection (so the sniff can see the body).
    call = robust_spy.call_args
    fetched_arg = call.args[2] if len(call.args) > 2 else call.kwargs.get("content")
    assert fetched_arg == xlsx, (
        "detect_parser_robust was not given the fetched body bytes"
    )
    # And the structured parser the sniff returned was actually used to parse.
    fake_parser.parse.assert_awaited_once()
    doc_service.ingest.assert_awaited_once()


@pytest.mark.asyncio()
async def test_worker_imports_robust_detector_not_only_plain() -> None:
    """Guard: the worker module exposes ``detect_parser_robust`` (the canonical
    byte-sniff entrypoint). A regression that re-imports only plain
    ``detect_parser`` for the funnel would trip this."""
    from ragbot.interfaces.workers import document_worker

    assert hasattr(document_worker, "detect_parser_robust"), (
        "worker must import detect_parser_robust to share the canonical "
        "byte-sniff detection path with DocumentService"
    )
