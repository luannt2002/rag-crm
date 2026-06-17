"""PDF parser strategy — pypdfium2 text extraction with per-page chunking.

Emits one chunk per non-empty page so the chunking layer's section guard
can split across page boundaries without loading the whole document as a
single chunk. Header marker ``## Page N`` is prepended to each page so
downstream `_is_table_line` / heading detectors keep working uniformly
with markdown / docx output.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any

import structlog

from ragbot.shared.constants import (
    DEFAULT_PDF_MAX_BYTES,
    DEFAULT_PDF_PARSE_CONCURRENCY,
)

logger = structlog.get_logger(__name__)


_PDF_MIME: str = "application/pdf"
_PDF_EXT: str = ".pdf"

# Cap inflight pdfium native allocations across the worker. A burst of
# uploads otherwise multiplies the per-document buffer + page cache and
# blows the 1GB worker RSS budget even when each file fits under the cap.
_PARSE_SEMAPHORE: asyncio.Semaphore = asyncio.Semaphore(DEFAULT_PDF_PARSE_CONCURRENCY)


def _pypdfium2_available() -> bool:
    try:
        import pypdfium2  # noqa: F401

        return True
    except ImportError:
        return False


class PdfParser:
    """PDF parser — one chunk per page; emits ``## Page N`` section header."""

    def __init__(self, **_: object) -> None:
        if not _pypdfium2_available():
            raise ImportError(
                "pypdfium2 is not installed; add to pyproject or set "
                "system_config.document_parser_provider='null'."
            )

    @staticmethod
    def get_provider_name() -> str:
        return "pdf"

    def supports(self, mime_type: str, file_ext: str) -> bool:
        return (
            (mime_type or "").strip().lower() == _PDF_MIME
            or (file_ext or "").strip().lower() == _PDF_EXT
        )

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        if len(content) > DEFAULT_PDF_MAX_BYTES:
            raise ValueError(
                f"PDF too large: {len(content)} bytes (max {DEFAULT_PDF_MAX_BYTES})",
            )

        async with _PARSE_SEMAPHORE:
            # Local import — preserves registry fail-soft path when dep missing.
            import pypdfium2 as pdfium

            chunks: list[dict[str, Any]] = []
            pdf = pdfium.PdfDocument(BytesIO(content))
            try:
                for page_index in range(len(pdf)):
                    page = pdf[page_index]
                    textpage = None
                    try:
                        textpage = page.get_textpage()
                        page_text = (textpage.get_text_range() or "").strip()
                    finally:
                        # textpage holds a separate native handle — release it
                        # before the page handle so pdfium can reclaim both.
                        if textpage is not None:
                            try:
                                textpage.close()
                            except AttributeError:
                                pass
                        page.close()

                    if not page_text:
                        continue

                    page_no = page_index + 1
                    chunks.append({
                        "content": f"## Page {page_no}\n\n{page_text}",
                        "metadata": {
                            "page_number": page_no,
                            "file_name": file_name,
                            "parser": self.get_provider_name(),
                        },
                    })
            finally:
                pdf.close()

        logger.info(
            "pdf_parsed",
            file_name=file_name,
            pages=len(chunks),
            bytes=len(content),
        )
        return chunks


__all__ = ["PdfParser"]
