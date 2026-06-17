"""DOCX parser strategy — python-docx, preserves headings + table structure.

Emits one chunk per logical block: each top-level heading starts a new chunk
that accumulates its body paragraphs until the next top-level heading. Tables
are rendered as pipe-separated markdown rows so the downstream
``_is_table_line`` detector keeps tables intact.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from zipfile import BadZipFile

import structlog

from ragbot.shared.constants import DEFAULT_DOCX_MAX_BYTES

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

        # Pre-render all paragraphs into a stream of (kind, text) tokens.
        tokens: list[tuple[str, str, int]] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style_name = para.style.name if para.style else ""
            level = _heading_level(style_name)
            if level is not None:
                tokens.append(("heading", text, level))
            else:
                tokens.append(("para", text, 0))

        # Append tables AFTER paragraphs (python-docx exposes them separately).
        # Each table is its own block so chunkers can keep it atomic.
        table_blocks: list[str] = []
        for table in doc.tables:
            rows: list[str] = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if not rows:
                continue
            n_cols = len(table.rows[0].cells)
            separator = "| " + " | ".join(["---"] * n_cols) + " |"
            table_md = rows[0] + "\n" + separator + "\n" + "\n".join(rows[1:])
            table_blocks.append(table_md)

        # Walk the heading/para stream, emitting one chunk per top-level
        # heading. Body text and sub-headings join the active chunk.
        chunks: list[dict[str, Any]] = []
        active_lines: list[str] = []
        active_meta: dict[str, Any] = {}
        block_index = 0

        def _flush() -> None:
            nonlocal block_index, active_lines, active_meta
            if not active_lines:
                return
            chunks.append({
                "content": "\n\n".join(active_lines).strip(),
                "metadata": {
                    **active_meta,
                    "block_index": block_index,
                    "file_name": file_name,
                    "parser": "docx",
                },
            })
            block_index += 1
            active_lines = []
            active_meta = {}

        for kind, text, level in tokens:
            if kind == "heading" and level == 1:
                _flush()
                active_meta = {"heading": text, "heading_level": 1}
                active_lines.append(f"# {text}")
            elif kind == "heading":
                marker = "#" * level
                active_lines.append(f"{marker} {text}")
                if "heading" not in active_meta:
                    active_meta = {"heading": text, "heading_level": level}
            else:
                active_lines.append(text)
        _flush()

        # If document had no headings at all but did have paragraphs,
        # _flush already emitted one chunk above. Tables become trailing
        # chunks so tabular content is queryable on its own.
        for table_md in table_blocks:
            chunks.append({
                "content": table_md,
                "metadata": {
                    "block_kind": "table",
                    "block_index": block_index,
                    "file_name": file_name,
                    "parser": "docx",
                },
            })
            block_index += 1

        logger.info(
            "docx_parsed",
            file_name=file_name,
            chunks=len(chunks),
            bytes=len(content),
        )
        return chunks


__all__ = ["DocxParser"]
