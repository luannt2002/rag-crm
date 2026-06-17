"""MarkdownParser strategy — unit tests.

Verifies Protocol conformance, mime/ext detection, YAML front-matter strip,
H1/H2 section split, and registry-level routing.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.document_parser_port import DocumentParserPort
from ragbot.infrastructure.parser.markdown_parser import MarkdownParser


def test_markdown_parser_supports_mimes_and_exts() -> None:
    p = MarkdownParser()
    assert p.supports("text/markdown", ".md") is True
    assert p.supports("text/x-markdown", ".markdown") is True
    assert p.supports("", ".md") is True
    assert p.supports("text/markdown", "") is True
    # Post-2026-05-26: plain text aliases through the markdown parser so
    # .txt corpora (legal documents, transcripts) ingest correctly.
    assert p.supports("text/plain", ".txt") is True
    assert p.supports("application/pdf", ".pdf") is False
    assert p.get_provider_name() == "markdown"
    assert isinstance(p, DocumentParserPort)


@pytest.mark.asyncio
async def test_markdown_parser_strips_yaml_front_matter() -> None:
    raw = (
        b"---\n"
        b"title: doc\n"
        b"author: anyone\n"
        b"---\n"
        b"# Heading One\n\n"
        b"Body text under heading one.\n"
    )
    parser = MarkdownParser()
    chunks = await parser.parse(raw, file_name="x.md")

    assert len(chunks) == 1
    assert chunks[0]["content"].startswith("# Heading One")
    assert "title: doc" not in chunks[0]["content"]
    assert "author: anyone" not in chunks[0]["content"]
    assert chunks[0]["metadata"]["heading"] == "Heading One"
    assert chunks[0]["metadata"]["parser"] == "markdown"
    assert chunks[0]["metadata"]["file_name"] == "x.md"


@pytest.mark.asyncio
async def test_markdown_parser_splits_on_top_level_headings() -> None:
    raw = (
        b"# Section 1\n\nAlpha body.\n\n"
        b"## Sub of 1\n\nMore alpha.\n\n"
        b"# Section 2\n\nBeta body.\n"
    )
    parser = MarkdownParser()
    chunks = await parser.parse(raw, file_name="multi.md")

    # H1 and H2 are both top-level splits per parser contract.
    assert len(chunks) == 3
    headings = [c["metadata"].get("heading") for c in chunks]
    assert headings == ["Section 1", "Sub of 1", "Section 2"]
    assert chunks[0]["content"].startswith("# Section 1")
    assert "Alpha body." in chunks[0]["content"]
    assert chunks[1]["content"].startswith("## Sub of 1")
    assert "More alpha." in chunks[1]["content"]
    assert chunks[2]["content"].startswith("# Section 2")
    assert "Beta body." in chunks[2]["content"]


@pytest.mark.asyncio
async def test_markdown_parser_no_headings_returns_single_chunk() -> None:
    raw = b"Just a paragraph.\n\nAnother line."
    parser = MarkdownParser()
    chunks = await parser.parse(raw, file_name="flat.md")

    assert len(chunks) == 1
    assert "Just a paragraph." in chunks[0]["content"]
    assert "heading" not in chunks[0]["metadata"]


@pytest.mark.asyncio
async def test_markdown_parser_rejects_oversized() -> None:
    from ragbot.shared.constants import DEFAULT_MARKDOWN_MAX_BYTES

    parser = MarkdownParser()
    too_big = b"# x\n" + b"x" * (DEFAULT_MARKDOWN_MAX_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        await parser.parse(too_big, file_name="huge.md")


def test_registry_routes_new_providers() -> None:
    """detect_parser routes pdf/docx/markdown mimes to the new adapters."""
    from ragbot.infrastructure.parser.registry import detect_parser, list_providers

    providers = list_providers()
    assert "pdf" in providers
    assert "docx" in providers
    assert "markdown" in providers

    md = detect_parser("text/markdown", ".md")
    assert md is not None
    assert md.get_provider_name() == "markdown"
