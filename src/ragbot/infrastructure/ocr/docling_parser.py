"""Docling OCR adapter — implements OCRPort for layout-aware parsing.

Docling (IBM, Apache 2.0) extracts headings / tables / text preserving
structure. Install: ``pip install docling``. When the dependency is missing
the parser raises ImportError; callers pick a fallback based on system
config ``parser_engine`` so installation is opt-in.
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any

import httpx
import structlog

from ragbot.application.ports.ocr_port import OCRPort, ParsedDocument
from ragbot.domain.entities.document import Block
from ragbot.shared.constants import DEFAULT_HTTP_TIMEOUT_S, DEFAULT_LANGUAGE
from ragbot.shared.context_buffer import attach_context_buffer

logger = structlog.get_logger(__name__)

_SUPPORTED_MIMES: frozenset[str] = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/html",
    "text/markdown",
})


def _docling_available() -> bool:
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


class DoclingParser(OCRPort):
    """Layout-aware parser powered by Docling, with automatic fallback.

    If ``docling`` is not installed at init time, instances raise
    ImportError so the caller can fall back to SimpleTextParser.
    """

    def __init__(self, *, http_timeout_s: float = float(DEFAULT_HTTP_TIMEOUT_S)) -> None:
        if not _docling_available():
            raise ImportError(
                "docling is not installed; run `pip install docling` or set "
                "system_config parser_engine='simple'"
            )
        self._client = httpx.AsyncClient(timeout=http_timeout_s)

    def supported_mimes(self) -> frozenset[str]:
        return _SUPPORTED_MIMES

    async def parse(
        self,
        source: str | bytes,
        *,
        mime_type_hint: str | None = None,
    ) -> ParsedDocument:
        data = await self._resolve_bytes(source)
        loop = asyncio.get_running_loop()
        blocks, page_count = await loop.run_in_executor(
            None, self._parse_bytes, data, mime_type_hint or "",
        )
        # AdapChunk Layer 2 — populate context_before/after on atomic
        # blocks (TABLE / FORMULA / IMAGE / CODE) so retrieval matches
        # surrounding prose. No-op when feature flag is OFF.
        blocks = attach_context_buffer(blocks)
        # Domain-neutral: parser does NOT detect language. Caller overrides
        # via per-bot ``bots.language`` column. See ``DEFAULT_LANGUAGE`` rationale.
        return ParsedDocument(
            blocks=blocks,
            language=DEFAULT_LANGUAGE,
            page_count=page_count,
            metadata={"parser": "docling"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── internal ────────────────────────────────────────────────────────
    async def _resolve_bytes(self, source: str | bytes) -> bytes:
        if isinstance(source, bytes):
            return source
        r = await self._client.get(source)
        r.raise_for_status()
        return r.content

    def _parse_bytes(self, data: bytes, mime_hint: str) -> tuple[list[Block], int]:
        # Docling import deferred: it's an optional dep. DoclingParser.__init__
        # has already verified the module is importable, so this is cheap here.
        from docling.document_converter import DocumentConverter

        suffix = _suffix_for_mime(mime_hint, data)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            converter = DocumentConverter()
            try:
                result = converter.convert(tmp.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("docling_convert_failed", error=str(exc)[:200])
                raise

        doc = result.document
        blocks: list[Block] = []
        pages: set[int] = set()

        for item in doc.iterate_items():
            content = (getattr(item, "text", None) or "").strip()
            if not content:
                continue

            label = (getattr(item, "label", "") or "").lower()
            block_type = _label_to_block_type(label)
            page_no = getattr(item, "page_no", 0) or 0
            if page_no:
                pages.add(page_no)

            blocks.append(Block(
                type=block_type,
                content=content,
                is_atomic=block_type in ("HEADING", "TABLE", "CODE", "FORMULA", "IMAGE"),
                page_number=page_no or None,
                ocr_metadata={"docling_label": label} if label else {},
            ))

        page_count = len(pages) if pages else getattr(doc, "page_count", 0) or 1
        logger.info("docling_parse_complete", blocks=len(blocks), pages=page_count)
        return blocks, page_count


def _label_to_block_type(label: str) -> str:
    # Map Docling label strings → domain BlockType Literal.
    if "heading" in label or "title" in label or "section_header" in label:
        return "HEADING"
    if "table" in label:
        return "TABLE"
    if "code" in label:
        return "CODE"
    if "formula" in label or "equation" in label:
        return "FORMULA"
    if "figure" in label or "picture" in label or "image" in label:
        return "IMAGE"
    if "list" in label:
        return "LIST"
    return "TEXT"


def _suffix_for_mime(mime_hint: str, data: bytes) -> str:
    if "pdf" in mime_hint or data[:4] == b"%PDF":
        return ".pdf"
    if "wordprocessing" in mime_hint or data[:4] == b"PK\x03\x04":
        return ".docx"
    if "html" in mime_hint:
        return ".html"
    if "markdown" in mime_hint:
        return ".md"
    return ".bin"


__all__ = ["DoclingParser"]
