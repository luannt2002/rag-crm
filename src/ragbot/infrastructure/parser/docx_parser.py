"""DOCX parser strategy — python-docx, preserves headings + table structure.

Emits one chunk per logical block: each top-level heading starts a new chunk
that accumulates its body paragraphs until the next top-level heading. Tables
are rendered as pipe-separated markdown rows so the downstream
``_is_table_line`` detector keeps tables intact.
"""

from __future__ import annotations

from io import BytesIO
from zipfile import BadZipFile

import structlog

from ragbot.shared.constants import DEFAULT_DOCX_MAX_BYTES
from ragbot.shared.tabular_markdown import rows_to_structured_markdown

logger = structlog.get_logger(__name__)


_DOCX_MIME: str = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_DOCX_EXT: str = ".docx"


def _python_docx_available() -> bool:
    try:
        import docx  # noqa: F401

        return True
    except ImportError:
        return False


def _heading_level(style_name: str) -> int | None:
    """Return 1..3 for Heading 1/2/3 styles, else None."""
    name = (style_name or "").strip().lower()
    if not name.startswith("heading"):
        return None
    tail = name[len("heading"):].strip()
    if tail.isdigit():
        level = int(tail)
        if 1 <= level <= 3:
            return level
    return None


class DocxParser:
    """DOCX parser — heading-aware block chunker with markdown table rendering."""

    def __init__(self, **_: object) -> None:
        if not _python_docx_available():
            raise ImportError(
                "python-docx is not installed; add to pyproject or set "
                "system_config.document_parser_provider='null'."
            )

    @staticmethod
    def get_provider_name() -> str:
        return "docx"

    def supports(self, mime_type: str, file_ext: str) -> bool:
        return (
            (mime_type or "").strip().lower() == _DOCX_MIME
            or (file_ext or "").strip().lower() == _DOCX_EXT
        )

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        if len(content) > DEFAULT_DOCX_MAX_BYTES:
            raise ValueError(
                f"DOCX too large: {len(content)} bytes (max {DEFAULT_DOCX_MAX_BYTES})",
            )

        # Local import — preserves registry fail-soft path when dep missing.
        from docx import Document as DocxDocument

        try:
            doc = DocxDocument(BytesIO(content))
        except (KeyError, ValueError, OSError, BadZipFile) as exc:
            raise ValueError(
                f"Invalid or corrupt DOCX: {type(exc).__name__}: {str(exc)[:120]}",
            ) from exc

        # Walk the document body in DOCUMENT ORDER (paragraphs AND tables
        # interleaved) and emit ONE structured-markdown document — so each table
        # stays UNDER its preceding heading instead of being appended at the end
        # bound to the wrong section (AdapChunk L1 + B3). Same canonical markdown
        # form as the Kreuzberg / tabular parsers.
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        parts: list[str] = []
        for child in doc.element.body.iterchildren():
            if child.tag == qn("w:p"):
                para = Paragraph(child, doc)
                text = para.text.strip()
                if not text:
                    continue
                level = _heading_level(para.style.name if para.style else "")
                parts.append(f"{'#' * level} {text}" if level else text)
            elif child.tag == qn("w:tbl"):
                # Route the raw cell matrix through the canonical converter instead
                # of hardcoding rows[0]=header — inherits multi-row-header merge,
                # section detection, blank-row/merged-cell recovery + column labels.
                table = Table(child, doc)
                cell_rows = [[c.text.strip() for c in row.cells] for row in table.rows]
                md = rows_to_structured_markdown(cell_rows)
                if md.strip():
                    parts.append(md)

        markdown = "\n\n".join(parts).strip()
        if not markdown:
            return []
        heading_lines = sum(
            1 for ln in markdown.splitlines() if ln.lstrip().startswith("#")
        )
        logger.info(
            "docx_parsed",
            file_name=file_name,
            bytes=len(content),
            markdown_chars=len(markdown),
            heading_lines=heading_lines,
        )
        return [
            {
                "content": markdown,
                "metadata": {
                    "file_name": file_name,
                    "parser": "docx",
                    "format": "markdown",
                    "heading_lines": heading_lines,
                },
            }
        ]


__all__ = ["DocxParser"]
