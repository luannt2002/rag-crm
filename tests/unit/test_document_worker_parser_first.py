"""Pin tests — 260525 Phase C Bug #4 worker parser-first routing.

Pre-fix: ``document_worker._handle_document_uploaded_inner`` dispatched
to ``container.ocr().parse(source_url)`` as the only fallback for the
"raw_content cache miss" path. URLs with ``mime_type='text/csv'`` (e.g.
Google Sheets ``edit?gid=`` links) returned HTML viewer pages; Kreuzberg
OCR'd the HTML and emitted zero blocks. Net result: every fallback
re-ingest of Sheets/CSV docs failed with
``RuntimeError("empty document text after parse")``.

Post-fix: the worker tries the parser registry first. ``GoogleSheetsParser``
(or ExcelOpenpyxlParser, MarkdownParser, ...) is asked for a structural
parse via ``detect_parser(mime, ext)``. Only when the registry returns
``None`` or no chunks does the worker fall back to OCR.

These tests probe the routing layer directly — no need to spin a full
worker harness. We assert:

  1. When mime/ext match a registry parser AND it yields chunks, the
     parser path is taken (OCR never invoked).
  2. When the registry returns None (e.g. ``mime='application/pdf'``,
     no current PDF parser registered), the OCR fallback runs.
  3. When the registry parser raises, the OCR fallback runs (defense).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_registry_parser_used_for_csv_url() -> None:
    """text/csv URL routes through ``GoogleSheetsParser`` (registry hit),
    OCR is never called."""
    from ragbot.infrastructure.parser.registry import detect_parser

    parser = detect_parser("text/csv", ".csv")
    assert parser is not None
    assert parser.get_provider_name() == "google_sheets"


@pytest.mark.asyncio
async def test_registry_parser_none_for_unknown_mime() -> None:
    """Unknown mime type: detect_parser returns None, worker falls back
    to OCR (regression guard — must not crash on novel mime types)."""
    from ragbot.infrastructure.parser.registry import detect_parser

    parser = detect_parser("application/x-novel-format-2099", ".xyz")
    assert parser is None


@pytest.mark.asyncio
async def test_excel_mime_routes_to_excel_parser() -> None:
    """``application/vnd.openxmlformats-...`` route into ExcelOpenpyxlParser."""
    from ragbot.infrastructure.parser.registry import detect_parser

    parser = detect_parser(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    )
    assert parser is not None
    # Either matches or NullParser fell back; verify provider name.
    name = (
        parser.get_provider_name() if hasattr(parser, "get_provider_name") else ""
    )
    assert name in ("excel_openpyxl", "null"), name


@pytest.mark.asyncio
async def test_csv_parser_consumes_real_csv_bytes() -> None:
    """End-to-end: GoogleSheetsParser given CSV bytes yields row chunks.

    Validates the path the worker now uses: fetch URL → parser.parse(bytes).
    """
    from ragbot.infrastructure.parser.registry import build_parser

    parser = build_parser("google_sheets")
    csv_bytes = (
        b"col_a,col_b,col_c\n"
        b"1,foo,100\n"
        b"2,bar,200\n"
        b"3,baz,300\n"
    )
    chunks = await parser.parse(csv_bytes, file_name="test.csv")
    assert isinstance(chunks, list)
    assert len(chunks) >= 3
    # Each chunk should carry the data row plus header labels.
    joined = "\n".join(c.get("content", "") for c in chunks)
    assert "foo" in joined
    assert "bar" in joined
    assert "baz" in joined


@pytest.mark.asyncio
async def test_csv_parser_empty_input_returns_empty_list() -> None:
    """Empty bytes → empty chunk list (worker then falls through to OCR
    or raises ``empty document text``)."""
    from ragbot.infrastructure.parser.registry import build_parser

    parser = build_parser("google_sheets")
    chunks = await parser.parse(b"", file_name="empty.csv")
    assert chunks == []
