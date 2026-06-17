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
async def test_parse_csv_bytes_emits_one_chunk_per_data_row() -> None:
    """Phase 1: feed Sheets-CSV bytes → expect ≥ N data rows back.

    Today this raises NotImplementedError → test fails (RED). Once Phase 1
    ships, the parser must yield one chunk per data row with the column
    semantics preserved.
    """
    csv_bytes = (
        b"Topic,Dich vu,Vung,Gia\n"
        b"Bang gia triet long,Triet long Diode Laser,Mep,899000\n"
        b"Bang gia triet long,Triet long Diode Laser,Mat,1499000\n"
        b"Bang gia triet long,Triet long Diode Laser,Nach,1199000\n"
    )
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="bang_gia.csv")

    assert isinstance(chunks, list), "parse() must return a list of chunk dicts"
    assert len(chunks) >= 3, f"expected ≥ 3 data rows, got {len(chunks)}"
    contents = [c.get("content", "") for c in chunks]
    assert any("Mep" in c for c in contents), "Mep row missing from output"
    assert any("Mat" in c for c in contents), "Mat row missing from output"
    assert any("Nach" in c for c in contents), "Nach row missing from output"


@pytest.mark.asyncio
async def test_parse_empty_returns_empty_list() -> None:
    parser = GoogleSheetsParser()
    chunks = await parser.parse(b"", file_name="empty.csv")
    assert chunks == [], "empty input must return [] (not raise, not None)"
