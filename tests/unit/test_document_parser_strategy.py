"""Document Parser Strategy registry — unit tests.

Covers:
- NullParser.supports always False, parse raises NotImplementedError.
- ExcelOpenpyxlParser MIME / extension match.
- ExcelOpenpyxlParser parses an in-memory 3-row workbook (skipped when
  ``openpyxl`` is not installed — registry fails soft anyway).
- Registry fallback to NullParser on unknown / empty provider.
- ``detect_parser`` returns ExcelOpenpyxlParser for ``.xlsx`` MIME when
  the dep is available; otherwise returns ``None``.
"""

from __future__ import annotations

import importlib

import pytest

from ragbot.application.ports.document_parser_port import DocumentParserPort
from ragbot.infrastructure.parser.null_parser import NullParser
from ragbot.infrastructure.parser.registry import (
    build_parser,
    detect_parser,
    list_providers,
)


_OPENPYXL = importlib.util.find_spec("openpyxl") is not None
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_null_parser_does_not_support_anything() -> None:
    p = NullParser()
    assert p.supports(_XLSX_MIME, ".xlsx") is False
    assert p.supports("text/plain", ".txt") is False
    assert p.supports("", "") is False
    assert p.get_provider_name() == "null"
    assert isinstance(p, DocumentParserPort)


@pytest.mark.asyncio
async def test_null_parser_parse_raises() -> None:
    p = NullParser()
    with pytest.raises(NotImplementedError):
        await p.parse(b"\x00\x01", file_name="x.xlsx")


@pytest.mark.skipif(not _OPENPYXL, reason="openpyxl not installed")
def test_excel_openpyxl_supports_xlsx() -> None:
    from ragbot.infrastructure.parser.excel_openpyxl_parser import ExcelOpenpyxlParser

    p = ExcelOpenpyxlParser()
    assert p.supports(_XLSX_MIME, ".xlsx") is True
    assert p.supports("text/csv", ".csv") is False
    # Extension-only match should still work even with empty MIME.
    assert p.supports("", ".xlsx") is True
    assert p.get_provider_name() == "excel_openpyxl"


@pytest.mark.skipif(not _OPENPYXL, reason="openpyxl not installed")
@pytest.mark.asyncio
async def test_excel_openpyxl_parses_3row_sheet() -> None:
    from io import BytesIO

    import openpyxl

    from ragbot.infrastructure.parser.excel_openpyxl_parser import ExcelOpenpyxlParser

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Prices"
    ws.append(["item", "price", "unit"])
    ws.append(["alpha", 10, "vnd"])
    ws.append(["beta", 20, "vnd"])
    ws.append(["gamma", 30, "vnd"])
    buf = BytesIO()
    wb.save(buf)
    raw = buf.getvalue()

    parser = ExcelOpenpyxlParser()
    chunks = await parser.parse(raw, file_name="sample.xlsx")

    # One structured-markdown document (AdapChunk L1), not one chunk per row.
    assert len(chunks) == 1
    md = chunks[0]["content"]
    assert chunks[0]["metadata"]["format"] == "markdown"
    assert chunks[0]["metadata"]["parser"] == "excel_openpyxl"
    # Markdown table preserving header + every row's values (B1/B2).
    assert "| item | price | unit |" in md
    assert "| alpha | 10 | vnd |" in md
    assert "| beta | 20 | vnd |" in md
    assert "| gamma | 30 | vnd |" in md


def test_registry_default_is_null() -> None:
    # Empty / None / unknown all funnel to NullParser.
    assert isinstance(build_parser(None), NullParser)
    assert isinstance(build_parser(""), NullParser)
    assert isinstance(build_parser("does_not_exist_xyz"), NullParser)
    # Registered keys appear in list_providers and are stable-sorted.
    providers = list_providers()
    assert "null" in providers
    assert "excel_openpyxl" in providers
    assert "google_sheets" in providers
    assert providers == sorted(providers)


def test_detect_parser_xlsx_returns_excel_provider() -> None:
    detected = detect_parser(_XLSX_MIME, ".xlsx")
    if _OPENPYXL:
        assert detected is not None
        assert detected.get_provider_name() == "excel_openpyxl"
    else:
        # When openpyxl missing, registry falls back to NullParser, which
        # the detect loop skips → no real provider matches → None.
        assert detected is None


def test_detect_parser_unsupported_mime_returns_none() -> None:
    # text/plain + .txt now routes to the markdown parser (post-2026-05-26
    # plain-text alias). Use a genuinely unsupported pair to exercise the
    # "no provider matches" branch instead.
    assert detect_parser("application/octet-stream", ".bin") is None
