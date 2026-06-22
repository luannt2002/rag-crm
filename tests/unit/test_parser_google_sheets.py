"""Stream A Phase 1 — `GoogleSheetsParser` must parse Sheets-CSV bytes.

Today the class raises ``NotImplementedError`` (see
``infrastructure/parser/google_sheets_parser.py``). Phase 1 plugs in a
public-share CSV-export path so customer-supplied Google Sheets URLs (or
already-fetched raw CSV bytes) flow through the registry like Excel does.

Tests pin the post-Phase-1 contract:
  - ``supports()`` keeps reporting True for Sheets MIME (regression guard).
  - ``parse()`` accepts CSV bytes and returns one dict per data row with a
    populated ``content`` field — same shape ExcelOpenpyxlParser uses.
"""
from __future__ import annotations

import pytest

from ragbot.infrastructure.parser.google_sheets_parser import GoogleSheetsParser

_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"


def test_supports_sheets_mime() -> None:
    parser = GoogleSheetsParser()
    assert parser.supports(_SHEETS_MIME, "")
    assert not parser.supports("text/plain", ".txt")


def test_provider_name_stable() -> None:
    assert GoogleSheetsParser.get_provider_name() == "google_sheets"


@pytest.mark.asyncio
async def test_parse_csv_bytes_emits_structured_markdown() -> None:
    """The sheet is converted to ONE structured-markdown document (AdapChunk L1).

    A multi-table sheet keeps each sub-table under its own SECTION TITLE so the
    downstream chunker/extractor can bind rows to their service. Here a single
    table (header + 3 data rows) → one markdown table preserving every value.
    """
    csv_bytes = (
        b"Bang gia triet long\n"
        b"Topic,Dich vu,Vung,Gia\n"
        b"1,Triet long Diode Laser,Mep,899000\n"
        b"2,Triet long Diode Laser,Mat,1499000\n"
        b"3,Triet long Diode Laser,Nach,1199000\n"
    )
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="bang_gia.csv")

    assert isinstance(chunks, list) and len(chunks) == 1, "one structured-markdown doc"
    md = chunks[0]["content"]
    assert chunks[0]["metadata"]["format"] == "markdown"
    # Section title bound as a heading ABOVE the table (B3).
    assert "## Bang gia triet long" in md
    # Markdown table preserving the values + column semantics (B1/B2).
    assert "| Mep |" in md and "899000" in md
    assert "| Mat |" in md and "1499000" in md
    assert "| Nach |" in md and "1199000" in md


@pytest.mark.asyncio
async def test_parse_empty_returns_empty_list() -> None:
    parser = GoogleSheetsParser()
    chunks = await parser.parse(b"", file_name="empty.csv")
    assert chunks == [], "empty input must return [] (not raise, not None)"
