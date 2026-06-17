"""Stream A Phase 4.5 — customer Topic column → metadata.enriched_prefix.

Anthropic Contextual Retrieval boost: when a sheet has a column whose
header matches Topic / Context / Section / Mô tả (case + accent
insensitive), the parser lifts that value into the chunk's
``metadata.enriched_prefix`` and removes it from the chunk body so the
Topic phrase only adds embedding signal — it does not duplicate inside
the answerable content.

Sacred: customer Topic NEVER reaches the runtime LLM prompt; it lives
at the ingest/embedding boundary only.
"""
from __future__ import annotations

import pytest

from ragbot.infrastructure.parser.google_sheets_parser import GoogleSheetsParser


@pytest.mark.asyncio
async def test_topic_column_lifted_into_enriched_prefix() -> None:
    csv_bytes = (
        b"Topic,Dich vu,Vung,Gia\n"
        b"Bang gia triet long Diode Laser cho vung nho,Triet long Diode,Mep,899000\n"
        b"Bang gia triet long Diode Laser cho vung mat,Triet long Diode,Mat,1499000\n"
    )
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="bang_gia.csv")

    assert len(chunks) == 2
    for c in chunks:
        prefix = c["metadata"].get("enriched_prefix")
        assert prefix and "Bang gia triet long" in prefix, (
            f"Topic value not lifted into enriched_prefix: {c['metadata']}"
        )
        assert c["metadata"].get("enriched_prefix_source") == "customer_topic_column"
        # Topic must NOT echo into the body.
        assert "Topic:" not in c["content"], (
            f"Topic column leaked into chunk body: {c['content']!r}"
        )


@pytest.mark.asyncio
async def test_vn_motata_label_matched_accent_insensitive() -> None:
    csv_bytes = (
        b"Mo ta,Dich vu,Gia\n"
        b"Massage thu gian toan than,Massage body,500000\n"
    )
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="x.csv")
    assert chunks[0]["metadata"].get("enriched_prefix") == "Massage thu gian toan than"


@pytest.mark.asyncio
async def test_no_context_column_falls_back_to_no_prefix() -> None:
    """Sheet without a Topic-style header: enriched_prefix absent (LLM
    auto-extract still runs at U5 if configured)."""
    csv_bytes = b"Dich vu,Gia\nMassage body,500000\n"
    parser = GoogleSheetsParser()
    chunks = await parser.parse(csv_bytes, file_name="x.csv")
    assert "enriched_prefix" not in chunks[0]["metadata"]
