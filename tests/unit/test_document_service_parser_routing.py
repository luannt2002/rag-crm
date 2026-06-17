"""Phase 6 follow-up — DocumentService routes raw bytes through parser registry.

Verifies the wire between ``document_service.ingest`` and
``infrastructure.parser.registry.detect_parser``: when ``raw_bytes`` is
supplied and a parser handles the mime/ext, the parser's extracted text
overrides ``content``; otherwise the existing string pass-through runs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.application.services.document_service import DocumentService


# Mime constants — kept as module-level vars so the tests do NOT hardcode
# magic strings inline (matches CLAUDE.md zero-hardcode rule).
_MIME_CSV = "text/csv"
_MIME_XLSX = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_MIME_PLAIN = "text/plain"
_MIME_PDF = "application/pdf"
_MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_MIME_MARKDOWN = "text/markdown"


def _make_parser_mock(provider: str, extracted: str) -> MagicMock:
    """Build a MagicMock that quacks like DocumentParserPort."""
    parser = MagicMock()
    parser.get_provider_name = MagicMock(return_value=provider)
    parser.parse = AsyncMock(
        return_value=[{"content": extracted, "metadata": {"parser": provider}}],
    )
    return parser


def _make_service(parser_detector: Any) -> DocumentService:
    """Build a DocumentService with stubbed deps + the supplied detector."""
    settings = MagicMock()
    settings.embedding.model_name = "stub"
    settings.embedding.dimension = 8
    settings.embedding.model_version = "stub-v1"
    return DocumentService(
        session_factory=MagicMock(),
        embedder=MagicMock(),
        settings=settings,
        config_service=None,
        audit_logger=None,
        parser_detector=parser_detector,
    )


@pytest.mark.asyncio
async def test_pdf_mime_routes_to_pdf_parser() -> None:
    """PDF mime + raw_bytes → registry detector returns PdfParser stub."""
    pdf_parser = _make_parser_mock("pdf", "## Page 1\n\nbody")
    detector = MagicMock(return_value=pdf_parser)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"%PDF-1.4 stub", mime_type=_MIME_PDF, file_name="x.pdf",
    )

    detector.assert_called_once_with(_MIME_PDF, ".pdf")
    pdf_parser.parse.assert_awaited_once()
    assert extracted == "## Page 1\n\nbody"


@pytest.mark.asyncio
async def test_docx_mime_routes_to_docx_parser() -> None:
    """DOCX mime + raw_bytes → registry detector returns DocxParser stub."""
    docx_parser = _make_parser_mock("docx", "# Title\n\nbody text")
    detector = MagicMock(return_value=docx_parser)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"PK\x03\x04 stub", mime_type=_MIME_DOCX, file_name="report.docx",
    )

    detector.assert_called_once_with(_MIME_DOCX, ".docx")
    docx_parser.parse.assert_awaited_once()
    assert "# Title" in extracted


@pytest.mark.asyncio
async def test_markdown_mime_routes_to_markdown_parser() -> None:
    """Markdown mime + raw_bytes → registry detector returns MarkdownParser stub."""
    md_parser = _make_parser_mock("markdown", "# Heading\n\npara")
    detector = MagicMock(return_value=md_parser)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"# Heading\n\npara",
        mime_type=_MIME_MARKDOWN,
        file_name="notes.md",
    )

    detector.assert_called_once_with(_MIME_MARKDOWN, ".md")
    md_parser.parse.assert_awaited_once()
    assert extracted.startswith("# Heading")


@pytest.mark.asyncio
async def test_excel_mime_routes_to_excel_parser() -> None:
    """XLSX mime + raw_bytes → registry detector returns ExcelOpenpyxlParser stub."""
    xlsx_parser = _make_parser_mock("excel_openpyxl", "item: a | price: 10")
    detector = MagicMock(return_value=xlsx_parser)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"PK\x03\x04 stub", mime_type=_MIME_XLSX, file_name="prices.xlsx",
    )

    detector.assert_called_once_with(_MIME_XLSX, ".xlsx")
    xlsx_parser.parse.assert_awaited_once()
    assert "item: a" in extracted


@pytest.mark.asyncio
async def test_csv_mime_passthrough_when_no_parser() -> None:
    """CSV is not in the registry → detector returns None → caller falls back."""
    detector = MagicMock(return_value=None)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"a,b,c\n1,2,3\n", mime_type=_MIME_CSV, file_name="data.csv",
    )

    detector.assert_called_once_with(_MIME_CSV, ".csv")
    assert extracted is None


@pytest.mark.asyncio
async def test_plain_text_mime_passthrough_when_no_parser() -> None:
    """text/plain is not in the registry → detector returns None."""
    detector = MagicMock(return_value=None)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"hello world", mime_type=_MIME_PLAIN, file_name="doc.txt",
    )

    detector.assert_called_once_with(_MIME_PLAIN, ".txt")
    assert extracted is None


@pytest.mark.asyncio
async def test_parser_returning_empty_chunk_list_yields_empty_string() -> None:
    """Parser with no chunks → empty extracted text (caller decides)."""
    parser = MagicMock()
    parser.get_provider_name = MagicMock(return_value="pdf")
    parser.parse = AsyncMock(return_value=[])
    detector = MagicMock(return_value=parser)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"%PDF stub", mime_type=_MIME_PDF, file_name="empty.pdf",
    )

    assert extracted == ""


@pytest.mark.asyncio
async def test_parser_chunks_are_joined_preserving_section_markers() -> None:
    """Multi-chunk output joined with blank lines so chunker sees markers."""
    parser = MagicMock()
    parser.get_provider_name = MagicMock(return_value="pdf")
    parser.parse = AsyncMock(
        return_value=[
            {"content": "## Page 1\n\npage one body", "metadata": {}},
            {"content": "## Page 2\n\npage two body", "metadata": {}},
        ],
    )
    detector = MagicMock(return_value=parser)
    svc = _make_service(detector)

    extracted, _ = await svc._route_through_parser(
        b"%PDF", mime_type=_MIME_PDF, file_name="multi.pdf",
    )

    assert "## Page 1" in extracted
    assert "## Page 2" in extracted
    # Joiner uses blank line so downstream heading detector still matches.
    assert "\n\n## Page 2" in extracted


@pytest.mark.asyncio
async def test_route_returns_chunks_list_for_phase2_preserve() -> None:
    """Phase 2 (Stream A): _route_through_parser must expose parser_chunks
    so the chunking step can bypass smart_chunk for row-shaped input.
    """
    rows = [
        {"content": "Topic: bang gia | Vung: Mep | Gia: 899000",
         "metadata": {"row_index": 2}},
        {"content": "Topic: bang gia | Vung: Mat | Gia: 1499000",
         "metadata": {"row_index": 3}},
        {"content": "Topic: bang gia | Vung: Nach | Gia: 1199000",
         "metadata": {"row_index": 4}},
    ]
    parser = MagicMock()
    parser.get_provider_name = MagicMock(return_value="google_sheets")
    parser.parse = AsyncMock(return_value=rows)
    detector = MagicMock(return_value=parser)
    svc = _make_service(detector)

    extracted, parser_chunks = await svc._route_through_parser(
        b"a,b,c\n", mime_type="text/csv", file_name="bang_gia.csv",
    )

    assert parser_chunks is not None
    assert len(parser_chunks) == 3
    assert parser_chunks[0]["metadata"]["row_index"] == 2
    # Joined string still produced for legacy callers / observability.
    assert "Mep" in extracted and "Mat" in extracted and "Nach" in extracted


@pytest.mark.asyncio
async def test_route_returns_none_chunks_when_no_parser() -> None:
    """No parser match → both tuple slots are None (legacy passthrough)."""
    detector = MagicMock(return_value=None)
    svc = _make_service(detector)
    extracted, parser_chunks = await svc._route_through_parser(
        b"plain text", mime_type="text/plain", file_name="x.txt",
    )
    assert extracted is None
    assert parser_chunks is None


def test_file_ext_helper_handles_edge_cases() -> None:
    """File-extension helper: dot-prefix lowercase, empty for None / no dot."""
    assert DocumentService._file_ext_from("Doc.PDF") == ".pdf"
    assert DocumentService._file_ext_from("notes.MD") == ".md"
    assert DocumentService._file_ext_from(None) == ""
    assert DocumentService._file_ext_from("") == ""
    # No-dot files (e.g. README) → empty.
    assert DocumentService._file_ext_from("README") == ""


@pytest.mark.asyncio
async def test_default_parser_detector_is_registry_detect_parser() -> None:
    """When no detector injected, the service binds the registry default."""
    from ragbot.infrastructure.parser.registry import detect_parser as registry_default

    settings = MagicMock()
    settings.embedding.model_name = "stub"
    settings.embedding.dimension = 8
    settings.embedding.model_version = "stub-v1"
    svc = DocumentService(
        session_factory=MagicMock(),
        embedder=MagicMock(),
        settings=settings,
    )
    assert svc._parser_detector is registry_default
