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
    """The sheet is converted to ATOMIC row-as-chunks (B2 AdapChunk L1).

    A multi-table sheet keeps each sub-table under its own SECTION TITLE; each
    DATA ROW becomes its own chunk carrying that section + the column header
    (so a value binds to the right row/column, no cross-row packing).
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

    # Row-as-chunk: 3 data rows → ≥3 atomic chunks.
    assert isinstance(chunks, list) and len(chunks) >= 3, "atomic row-as-chunk"
    assert chunks[0]["metadata"]["format"] == "markdown"
    md_all = "\n".join(c["content"] for c in chunks)
    # Every value preserved across the row-chunks.
    assert "899000" in md_all and "1499000" in md_all and "1199000" in md_all
    # Each row chunk carries the section heading + the column header.
    mep = next(c["content"] for c in chunks if "Mep" in c["content"])
    assert "## Bang gia triet long" in mep, "section heading not bound into row chunk"
    assert "Vung" in mep and "Gia" in mep, "column header not bound into row chunk"
    # Atomic — Mep's chunk must not carry the other rows' regions/prices.
    assert "Mat" not in mep and "Nach" not in mep, "cross-row packing in one chunk"


@pytest.mark.asyncio
async def test_parse_empty_returns_empty_list() -> None:
    parser = GoogleSheetsParser()
    chunks = await parser.parse(b"", file_name="empty.csv")
    assert chunks == [], "empty input must return [] (not raise, not None)"
