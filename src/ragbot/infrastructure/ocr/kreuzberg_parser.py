"""Kreuzberg OCR adapter — implements OCRPort for layout-aware parsing.

Kreuzberg (Rust-pure, MIT/ELv2) extracts headings / tables / text from PDFs,
DOCX, HTML, Markdown and scanned image-only PDFs (Tesseract OCR for
``vie+eng``). Install: ``pip install kreuzberg`` (or the ``parsers`` optional
group). When the dependency is missing the parser raises ``ImportError`` at
init so ``ocr_factory`` can fall back to ``SimpleTextParser``.

Proof citation:
    # Kreuzberg v4 — Rust-pure document parser.
    # Source: https://github.com/Goldziher/kreuzberg
    # Benchmark: F1 ~91% layout (parity with Docling 91.4%), ~9× faster,
    # ~71MB install. Vietnamese OCR via Tesseract `vie` + EasyOCR `vi`.

Feature flag:
    ``system_config.kreuzberg_parser_enabled`` (default OFF) gates whether
    the engine is even constructed; ``ocr_factory.build_ocr_parser()``
    reads ``RAGBOT_PARSER_ENGINE=kreuzberg`` to select it.

Telemetry:
    structlog event ``kreuzberg_parse_done`` with
    ``step_name="kreuzberg_parse"``, ``feature_flag="kreuzberg_parser_enabled"``,
    ``duration_ms``, ``block_count``, ``atomic_count``, ``language``.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from ragbot.application.ports.ocr_port import OCRPort, ParsedDocument
from ragbot.domain.entities.document import Block
from ragbot.shared.mime_sniff import sniff_real_mime
from ragbot.shared.constants import (
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_KREUZBERG_MAX_BYTES,
    DEFAULT_KREUZBERG_OCR_LANGUAGE,
    DEFAULT_LANGUAGE,
    KREUZBERG_SUPPORTED_MIMES,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = structlog.get_logger(__name__)

_STEP_NAME: str = "kreuzberg_parse"
_FEATURE_FLAG: str = "kreuzberg_parser_enabled"
_MIMES_FROZEN: frozenset[str] = frozenset(KREUZBERG_SUPPORTED_MIMES)

# Element-type → BlockType. Tuple-based mapping (preserves insertion order
# semantics). Anything not matched falls through to "TEXT".
_BLOCK_TYPE_MATCHERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("heading", "title", "section_header"), "HEADING"),
    (("table",), "TABLE"),
    (("formula", "equation"), "FORMULA"),
    (("figure", "picture", "image"), "IMAGE"),
    (("code", "listing"), "CODE"),
    (("list", "list_item"), "LIST"),
)

# Block types where breaking the block across chunks loses meaning — the
# chunker must keep them whole. Mirrors AdapChunk §6.3 atomic block policy.
_ATOMIC_BLOCK_TYPES: frozenset[str] = frozenset({"HEADING", "TABLE", "FORMULA", "IMAGE", "CODE"})


def _kreuzberg_available() -> bool:
    try:
        import kreuzberg  # noqa: F401

        return True
    except ImportError:
        return False


def _element_type_to_block_type(element_type: str) -> str:
    """Map a Kreuzberg element-type label to a domain BlockType literal.

    Matching is case-insensitive + substring-based so vendor label
    variants (e.g. ``"section_header"`` vs ``"sectionHeading"``) land on
    the same domain type without each variant being enumerated.
    """
    label = (element_type or "").strip().lower()
    if not label:
        return "TEXT"
    for needles, block_type in _BLOCK_TYPE_MATCHERS:
        for needle in needles:
            if needle in label:
                return block_type
    return "TEXT"


def _suffix_for_mime(mime_hint: str, data: bytes) -> str:
    """Pick a file-suffix Kreuzberg can route by extension.

    Mirrors the docling adapter heuristic so the two parsers agree on
    detection rules (PDF magic / OOXML subtype / HTML / Markdown / image).
    The OOXML subtypes are distinguished by their resolved MIME (read from
    the zip manifest upstream), never by guessing a single suffix for any zip.
    """
    mime = (mime_hint or "").lower()
    if "pdf" in mime or data[:4] == b"%PDF":
        return ".pdf"
    if "spreadsheetml" in mime:
        return ".xlsx"
    if "presentationml" in mime:
        return ".pptx"
    if "wordprocessing" in mime or (mime == "" and data[:4] == b"PK\x03\x04"):
        return ".docx"
    if "html" in mime:
        return ".html"
    if "markdown" in mime:
        return ".md"
    if "png" in mime or data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if "jpeg" in mime or data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if "tiff" in mime:
        return ".tiff"
    return ".bin"


class KreuzbergParser(OCRPort):
    """Layout-aware OCR parser powered by Kreuzberg, with graceful fallback.

    If ``kreuzberg`` is not installed at init time, ``__init__`` raises
    :class:`ImportError` so ``ocr_factory`` can drop back to
    ``SimpleTextParser`` (Strategy + Null Object preserved).

    The constructor accepts ``ocr_language`` so callers can override the
    Tesseract language code per deployment (default lifted from
    ``DEFAULT_KREUZBERG_OCR_LANGUAGE``). Heading context is prepended on
    each block so downstream chunkers stay consistent with the Docling
    adapter.
    """

    def __init__(
        self,
        *,
        ocr_language: str = DEFAULT_KREUZBERG_OCR_LANGUAGE,
        http_timeout_s: float = float(DEFAULT_HTTP_TIMEOUT_S),
        max_bytes: int = DEFAULT_KREUZBERG_MAX_BYTES,
    ) -> None:
        if not _kreuzberg_available():
            raise ImportError(
                "kreuzberg is not installed; run `pip install kreuzberg` "
                "(or `pip install 'ragbot[parsers]'`) or set "
                "system_config.kreuzberg_parser_enabled=false to fall back "
                "to the default OCR engine.",
            )
        self._ocr_language = ocr_language or DEFAULT_KREUZBERG_OCR_LANGUAGE
        self._max_bytes = max_bytes
        self._client = httpx.AsyncClient(timeout=http_timeout_s)

    # ── OCRPort API ─────────────────────────────────────────────────────

    def supported_mimes(self) -> frozenset[str]:
        return _MIMES_FROZEN

    async def parse(
        self,
        source: str | bytes,
        *,
        mime_type_hint: str | None = None,
    ) -> ParsedDocument:
        data = await self._resolve_bytes(source)
        if len(data) > self._max_bytes:
            raise ValueError(
                f"Document too large for Kreuzberg: {len(data)} bytes "
                f"(max {self._max_bytes})",
            )

        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        blocks, page_count = await loop.run_in_executor(
            None,
            self._extract_blocks,
            data,
            mime_type_hint or "",
        )
        duration_ms = int((time.perf_counter() - started) * 1000)

        atomic_count = sum(1 for b in blocks if b.is_atomic)
        logger.info(
            "kreuzberg_parse_done",
            step_name=_STEP_NAME,
            feature_flag=_FEATURE_FLAG,
            duration_ms=duration_ms,
            block_count=len(blocks),
            atomic_count=atomic_count,
            page_count=page_count,
            language=self._ocr_language,
            bytes=len(data),
        )

        # Domain-neutral: parser does NOT detect language. Caller overrides
        # via per-bot ``bots.language`` column (matches Docling adapter).
        return ParsedDocument(
            blocks=blocks,
            language=DEFAULT_LANGUAGE,
            page_count=page_count,
            metadata={
                "parser": "kreuzberg",
                "ocr_language": self._ocr_language,
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── internal helpers ────────────────────────────────────────────────

    async def _resolve_bytes(self, source: str | bytes) -> bytes:
        if isinstance(source, bytes):
            return source
        r = await self._client.get(source)
        r.raise_for_status()
        return r.content

    def _extract_blocks(
        self,
        data: bytes,
        mime_hint: str,
    ) -> tuple[list[Block], int]:
        """Run Kreuzberg synchronously inside the event-loop executor.

        Returns ``(blocks, page_count)``. Heading context is prepended on
        each block (``Block.context_before``) so the chunker can keep
        atomic blocks coherent without a second pass.
        """
        # Local import — preserves registry fail-soft path when dep missing.
        import kreuzberg  # type: ignore[import-not-found]

        # this method runs in a THREAD executor (sync context), so it
        # must call the SYNC variant. kreuzberg>=4.9's ``extract_bytes`` is a
        # COROUTINE — calling it here returned an un-awaited coroutine whose
        # ``.elements`` is None → 0 blocks for EVERY document that reached this
        # fallback (images, .doc/.xls, unknown formats → DLQ). Prefer
        # ``extract_bytes_sync``; fall back to ``extract_bytes`` only for old
        # (<4.9) versions where it was itself synchronous.
        extract_bytes = (
            getattr(kreuzberg, "extract_bytes_sync", None)
            or getattr(kreuzberg, "extract_bytes", None)
        )
        if extract_bytes is None:  # pragma: no cover — defensive against vendor rename
            raise ImportError(
                "kreuzberg.extract_bytes_sync not found; "
                "kreuzberg>=4.0 required.",
            )

        # kreuzberg>=4.0 takes a POSITIONAL ``mime_type``; older versions
        # accepted a ``filename=`` kwarg instead. Try the modern positional
        # call first and fall back to the keyword form on TypeError so the
        # adapter works against both signatures.
        #
        # Resolve the real MIME from the bytes when the caller's hint is
        # ambiguous (octet-stream / empty). An XLSX/PPTX uploaded as
        # octet-stream must be routed by its OOXML subtype (read from the zip
        # manifest), not handed to the engine as a generic blob that gets
        # mis-detected as a Word document.
        resolved_mime = sniff_real_mime(data, "", mime_hint) or mime_hint
        mime_type_arg = resolved_mime or "application/octet-stream"
        try:
            result = extract_bytes(data, mime_type_arg)
        except TypeError:
            # Defensive fallback for older kreuzberg versions still on PyPI
            # mirrors that accepted ``filename=`` kwarg.
            suffix = _suffix_for_mime(resolved_mime, data)
            result = extract_bytes(
                data,
                filename=f"upload{suffix}",
                ocr_language=self._ocr_language,
                prepend_heading_context=True,
            )

        elements: Iterable[Any] = (
            getattr(result, "elements", None)
            or getattr(result, "blocks", None)
            or ()
        )
        blocks: list[Block] = []
        active_heading: str = ""
        pages: set[int] = set()

        for element in elements:
            content = (
                getattr(element, "text", None)
                or getattr(element, "content", None)
                or ""
            ).strip()
            if not content:
                continue
            element_type = (
                getattr(element, "element_type", None)
                or getattr(element, "type", None)
                or ""
            )
            block_type = _element_type_to_block_type(str(element_type))
            page_no_raw = getattr(element, "page_no", None) or getattr(element, "page_number", None)
            page_no = int(page_no_raw) if page_no_raw else None
            if page_no:
                pages.add(page_no)

            if block_type == "HEADING":
                active_heading = content
                context_before = ""
            else:
                context_before = active_heading

            blocks.append(
                Block(
                    type=block_type,
                    content=content,
                    is_atomic=block_type in _ATOMIC_BLOCK_TYPES,
                    context_before=context_before,
                    page_number=page_no,
                    ocr_metadata={
                        "kreuzberg_label": str(element_type) if element_type else "",
                    },
                ),
            )

        # kreuzberg populates ``.elements`` only for
        # layout/OCR extraction. A plain text-layer extraction returns
        # elements=None but a populated ``.content`` — without this fallback a
        # content-bearing document still yielded 0 blocks → "empty document text"
        # → DLQ. Build blocks from the recovered markdown so extraction NEVER
        # silently drops to zero when text was actually extracted (fail-loud floor).
        if not blocks:
            content_text = (getattr(result, "content", None) or "").strip()
            for para in content_text.split("\n\n"):
                para = para.strip()
                if not para:
                    continue
                is_heading = para.lstrip().startswith("#")
                if is_heading:
                    active_heading = para.lstrip("# ").strip()
                blocks.append(
                    Block(
                        type="HEADING" if is_heading else "TEXT",
                        content=para,
                        is_atomic=False,
                        context_before="" if is_heading else active_heading,
                        page_number=None,
                        ocr_metadata={"kreuzberg_label": "content_fallback"},
                    ),
                )

        page_count = (
            len(pages)
            if pages
            else int(getattr(result, "page_count", 0) or 0)
            or (1 if blocks else 0)
        )
        return blocks, page_count


__all__ = ["KreuzbergParser"]
