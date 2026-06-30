"""Customer Topic / Mô tả column — preserved in structured markdown.

The sheet parser now emits ONE structured-markdown document (AdapChunk L1)
instead of per-row chunks, so a customer-curated Topic / Context / Mô tả column
is no longer lifted into per-row ``metadata.enriched_prefix`` — it stays IN the
markdown table as its own column, so its phrasing still adds embedding signal
(Anthropic Contextual Retrieval) AND the section structure now carries the
context the Topic column used to substitute for.
"""
from __future__ import annotations

import pytest

from ragbot.infrastructure.parser.google_sheets_parser import GoogleSheetsParser


@pytest.mark.asyncio
async def test_topic_column_preserved_in_structured_markdown() -> None:
    csv_bytes = (
        b"Topic,Dich vu,Vung,Gia\n"
        b"Bang gia triet long Diode Laser cho vung nho,Triet long Diode,Mep,899000\n"
        b"Bang gia triet long Diode Laser cho vung mat,Triet long Diode,Mat,1499000\n"
    )
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="bang_gia.csv")

    # Row-as-chunk (B2): 2 data rows → ≥2 atomic chunks.
    assert len(chunks) >= 2
    md = "\n".join(c["content"] for c in chunks)
    assert chunks[0]["metadata"]["format"] == "markdown"
    # The Topic phrasing stays embedded in the row chunk (still an embedding signal).
    assert "Bang gia triet long" in md
    assert "| Mep |" in md and "899000" in md


@pytest.mark.asyncio
async def test_vn_motata_content_preserved() -> None:
    csv_bytes = (
        b"Mo ta,Dich vu,Gia\n"
        b"Massage thu gian toan than,Massage body,500000\n"
    )
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="x.csv")
    assert len(chunks) == 1
    assert "Massage thu gian toan than" in chunks[0]["content"]
    assert "500000" in chunks[0]["content"]


@pytest.mark.asyncio
async def test_plain_table_emits_markdown() -> None:
    csv_bytes = b"Dich vu,Gia\nMassage body,500000\n"
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="x.csv")
    assert len(chunks) == 1
    assert chunks[0]["metadata"]["format"] == "markdown"
    assert "| Massage body | 500000 |" in chunks[0]["content"]
