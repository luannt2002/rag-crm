"""KreuzbergMarkdownParser — structured-markdown output + registry precedence.

Regression guard for the 2026-06-19 flat-output bug: the legacy ``pdf`` parser
(pypdfium2) emitted zero ``#`` headings, collapsing chapter/article structure.
Kreuzberg with ``OutputFormat.MARKDOWN`` restores the hierarchy, and the registry
must route layout-rich formats to it ahead of the flat parser.
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.parser.kreuzberg_markdown_parser import (
    KreuzbergMarkdownParser,
)
from ragbot.infrastructure.parser.registry import detect_parser

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def test_supports_only_layout_rich_formats() -> None:
    parser = KreuzbergMarkdownParser()
    assert parser.supports("application/pdf", ".pdf") is True
    assert parser.supports("", ".pptx") is True
    assert parser.supports("text/html", "") is True
    # DOCX / XLSX / CSV / TXT deliberately stay on their dedicated lighter
    # parsers — Kreuzberg must NOT steal them.
    assert parser.supports(_DOCX_MIME, ".docx") is False
    assert parser.supports("text/csv", ".csv") is False
    assert parser.supports("", ".txt") is False


def test_provider_name_stable() -> None:
    assert KreuzbergMarkdownParser.get_provider_name() == "kreuzberg_markdown"


def test_registry_routes_pdf_to_kreuzberg_not_flat_pdf() -> None:
    """PDF must resolve to the structured ``kreuzberg_markdown`` parser, taking
    precedence over the legacy flat ``pdf`` (pypdfium2)."""
    parser = detect_parser("application/pdf", ".pdf")
    assert parser is not None
    assert parser.get_provider_name() == "kreuzberg_markdown"


async def test_parse_emits_markdown_headings() -> None:
    """Heading-bearing input → ``#`` markdown headings (the flat-output fix)."""
    html = (
        b"<html><body><h1>Chuong Mot</h1><p>noi dung</p>"
        b"<h2>Dieu Hai</h2><p>chi tiet</p></body></html>"
    )
    chunks = await KreuzbergMarkdownParser().parse(html, file_name="t.html")
    assert len(chunks) == 1
    block = chunks[0]
    assert block["metadata"]["parser"] == "kreuzberg_markdown"
    heading_lines = [
        line for line in block["content"].splitlines() if line.lstrip().startswith("#")
    ]
    assert "# Chuong Mot" in heading_lines
    assert any(h.startswith("##") for h in heading_lines)
    assert block["metadata"]["heading_lines"] >= 2


async def test_parse_empty_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        await KreuzbergMarkdownParser().parse(b"   ", file_name="t.html")
