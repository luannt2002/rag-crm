"""Kreuzberg structured-Markdown parser — AdapChunk Layer-1 (the chosen winner).

The legacy ``pdf_parser`` (pypdfium2) and the bare ``kreuzberg.extract_file``
demo emit FLAT text: a Thông tư PDF came out with **0** ``#`` headings, chapter
/ article titles indistinguishable from body lines (verified 2026-06-19). That
starves the downstream AdapChunk HDT / table strategies of structure.

The fix is NOT a new heavyweight dependency — pyproject already pins
``kreuzberg>=4.3.0`` as the "AdapChunk Layer 1 primary parser" (97 formats, F1
~91% layout at parity with Docling, ~9× faster, ~71MB). The only missing piece
was the config flag: passing ``ExtractionConfig(output_format=OutputFormat.MARKDOWN)``
turns the SAME extractor from flat text into structured Markdown.

Proof (TT09.pdf, kreuzberg 4.9.9):
    default extract_bytes(data, mime)                 -> 0   ``#`` heading lines
    ExtractionConfig(output_format=MARKDOWN)          -> 72  ``#`` heading lines
        (# THÔNG TƯ … / # Chương I / ## Điều 1 / ## Điều 2 …)

Head-to-head, Docling FAILED on the same file (``requires accelerate`` + GB
torch models), so Kreuzberg is both lighter AND the only one that ran.

Registry precedence (strangler-fig): registered BEFORE ``pdf`` so it wins for
the formats it ``supports``. If the ``kreuzberg`` dep is absent (it lives in the
``parsers`` optional extra), ``build_parser`` catches the ``ImportError`` →
``NullParser`` and ``detect_parser`` drops through to the legacy ``pdf_parser``
— an uninstalled Kreuzberg is a no-op, never a boot break. DOCX / XLSX / CSV /
Markdown stay on their existing lightweight parsers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Final

import structlog

from ragbot.shared.constants import DEFAULT_PDF_MAX_BYTES
from ragbot.shared.structured_blocks import markdown_to_blocks

if TYPE_CHECKING:
    from ragbot.application.ports.document_parser_port import DocumentParserPort
    from ragbot.domain.entities.document import Block

logger = structlog.get_logger(__name__)

# Formats this parser owns. DOCX/XLSX/CSV intentionally excluded — already
# handled well by the lighter python-docx / openpyxl / csv parsers.
_KREUZBERG_MIMES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/html",
        "application/xhtml+xml",
    }
)
_KREUZBERG_EXTS: Final[frozenset[str]] = frozenset(
    {".pdf", ".pptx", ".html", ".htm", ".xhtml"}
)
_EXT_TO_MIME: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "application/xhtml+xml",
}


def _kreuzberg_available() -> bool:
    try:
        import kreuzberg  # noqa: F401
    except ImportError:
        return False
    return True


def _resolve_mime(file_name: str, content: bytes) -> str:
    """Pick a mime: file extension first, then Kreuzberg's byte sniffer."""
    ext = ""
    if "." in file_name:
        ext = "." + file_name.rsplit(".", 1)[-1].strip().lower()
    if ext in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext]
    try:
        import kreuzberg

        detected = kreuzberg.detect_mime_type_from_bytes(content)
        if detected:
            return str(detected)
    except (ImportError, ValueError, TypeError, OSError):
        pass
    return "application/pdf"


class KreuzbergMarkdownParser:
    """Structured-Markdown parser (PDF / PPTX / HTML) via Kreuzberg 4.x."""

    def __init__(self, **_: object) -> None:
        if not _kreuzberg_available():
            raise ImportError(
                "kreuzberg is not installed; install the 'parsers' extra "
                "(pip install '.[parsers]') or set "
                "system_config.document_parser_provider to another provider."
            )

    @staticmethod
    def get_provider_name() -> str:
        return "kreuzberg_markdown"

    def supports(self, mime_type: str, file_ext: str) -> bool:
        return (
            (mime_type or "").strip().lower() in _KREUZBERG_MIMES
            or (file_ext or "").strip().lower() in _KREUZBERG_EXTS
        )

    async def _extract_markdown(self, content: bytes, *, file_name: str) -> str:
        """Run Kreuzberg off the event loop → structured markdown string."""
        if len(content) > DEFAULT_PDF_MAX_BYTES:
            raise ValueError(
                f"File too large for Kreuzberg: {len(content)} bytes "
                f"(max {DEFAULT_PDF_MAX_BYTES})",
            )

        import kreuzberg
        from kreuzberg import ExtractionConfig, KreuzbergError, OutputFormat

        mime = _resolve_mime(file_name, content)
        config = ExtractionConfig(output_format=OutputFormat.MARKDOWN)

        # extract_bytes_sync is CPU-bound (layout/OCR inference) — run it off the
        # event loop so concurrent requests sharing the process aren't blocked.
        try:
            result = await asyncio.to_thread(
                kreuzberg.extract_bytes_sync, content, mime, config
            )
        except (KreuzbergError, RuntimeError, ValueError, OSError, TypeError) as exc:
            raise ValueError(
                f"Kreuzberg failed to parse {file_name}: "
                f"{type(exc).__name__}: {str(exc)[:160]}",
            ) from exc

        markdown = getattr(result, "content", "") or ""
        if not markdown.strip():
            raise ValueError(f"Kreuzberg produced empty markdown for {file_name}")
        return markdown

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        markdown = await self._extract_markdown(content, file_name=file_name)
        heading_lines = sum(
            1 for ln in markdown.splitlines() if ln.lstrip().startswith("#")
        )
        logger.info(
            "kreuzberg_markdown_parsed",
            file_name=file_name,
            bytes=len(content),
            markdown_chars=len(markdown),
            heading_lines=heading_lines,
        )
        # ONE block carrying the full structured markdown — the ``#`` hierarchy
        # is exactly what the downstream AdapChunk HDT / table strategies chunk
        # on. Re-splitting per page here would sever headings from their bodies.
        return [
            {
                "content": markdown,
                "metadata": {
                    "file_name": file_name,
                    "parser": self.get_provider_name(),
                    "format": "markdown",
                    "heading_lines": heading_lines,
                },
            }
        ]

    async def parse_blocks(self, content: bytes, *, file_name: str) -> list[Block]:
        """Emit a typed ``Block`` stream from the structured markdown."""
        markdown = await self._extract_markdown(content, file_name=file_name)
        return markdown_to_blocks(markdown)


__all__ = ["KreuzbergMarkdownParser"]
