"""Pin test: markdown parser must accept plain-text MIME + .txt extension.

Was a production gap until 2026-05-26: HTTP /documents/upload with a UTF-8
.txt body returned ``{"chunks":1,"embedded":true}`` but the chunk had
``content=""`` because no parser in the registry supported ``text/plain``
nor ``.txt``. The markdown parser path is the correct fallback — it
decodes UTF-8, splits by H1/H2, and falls through to sentence chunking
for content without headings, which is exactly what a plain .txt corpus
(legal documents, transcripts) needs.

This test pins the supports() contract so a future refactor cannot drop
the plain-text alias silently.
"""

from __future__ import annotations

from ragbot.infrastructure.parser.markdown_parser import MarkdownParser


def test_markdown_parser_supports_plain_text_mime() -> None:
    parser = MarkdownParser()
    assert parser.supports("text/plain", ".txt") is True


def test_markdown_parser_supports_txt_extension_alone() -> None:
    parser = MarkdownParser()
    assert parser.supports("application/octet-stream", ".txt") is True


def test_markdown_parser_still_supports_markdown_mime() -> None:
    parser = MarkdownParser()
    assert parser.supports("text/markdown", ".md") is True
    assert parser.supports("text/x-markdown", ".markdown") is True


def test_markdown_parser_rejects_unrelated_types() -> None:
    parser = MarkdownParser()
    assert parser.supports("application/pdf", ".pdf") is False
    assert parser.supports("text/csv", ".csv") is False
