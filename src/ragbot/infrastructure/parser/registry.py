"""Document parser strategy registry — DI factory based on provider key.

Pattern mirrors :mod:`ragbot.infrastructure.reranker.registry`. Caller
(`bootstrap.Container` or ingestion service) reads
``system_config.document_parser_provider`` and asks this module for the
matching :class:`DocumentParserPort` implementation.

Adding a new parser = drop a new file in this package and register the
class here. **No edits to ingest service or bootstrap.**

Wiring: ``DocumentService`` imports :func:`detect_parser` as its default
``parser_detector`` (see ``document_service.py:75`` + ``:332``) so the
hot ingest path routes through this registry on every request. The
``GoogleSheetsParser`` row-as-chunk impl ships as Stream A Phase 1,
``ExcelOpenpyxlParser`` ships row-as-chunk too, and Phase 2 preserves
that 1-row → 1-chunk shape end-to-end (no flatten + re-chunk).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.shared.mime_sniff import sniff_real_mime
from ragbot.infrastructure.parser.docx_parser import DocxParser
from ragbot.infrastructure.parser.kreuzberg_markdown_parser import (
    KreuzbergMarkdownParser,
)
from ragbot.infrastructure.parser.excel_openpyxl_parser import ExcelOpenpyxlParser
from ragbot.infrastructure.parser.google_sheets_parser import GoogleSheetsParser
from ragbot.infrastructure.parser.markdown_parser import MarkdownParser
from ragbot.infrastructure.parser.null_parser import NullParser
from ragbot.infrastructure.parser.pdf_parser import PdfParser
from ragbot.infrastructure.parser.vlm_image_parser import VlmImageParser

if TYPE_CHECKING:
    from collections.abc import Callable

    from ragbot.application.ports.document_parser_port import DocumentParserPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "null": NullParser,
    # Structured-markdown (pdf/pptx/html) via Kreuzberg + OutputFormat.MARKDOWN —
    # precedence over legacy ``pdf``. Fail-soft: if kreuzberg absent, build_parser
    # → NullParser and detect_parser drops through to ``pdf`` (pypdfium2).
    "kreuzberg_markdown": KreuzbergMarkdownParser,
    "excel_openpyxl": ExcelOpenpyxlParser,
    "google_sheets": GoogleSheetsParser,
    "pdf": PdfParser,
    "docx": DocxParser,
    "markdown": MarkdownParser,
    # Image captioner (PNG/JPEG/…) via a vision model. Constructed ONLY with injected
    # llm+spec (build_parser("vlm_image", llm=…, spec=…, record_tenant_id=…, trace_id=…));
    # detect_parser's no-arg probe raises TypeError → fail-soft skip, so this never
    # auto-fires — the worker selects it explicitly for image MIMEs when VLM is enabled.
    "vlm_image": VlmImageParser,
}


def build_parser(provider: str | None = None, **kwargs) -> "DocumentParserPort":
    """Construct the parser matching ``provider``.

    @param provider: registry key. ``None`` / unknown / empty → NullParser
        (warned). Heavy strategies (e.g. ExcelOpenpyxlParser) raise
        ``ImportError`` from ``__init__`` when their dep is missing; we
        catch that and fall back to NullParser so boot survives.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "document_parser_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullParser
    try:
        return cls(**kwargs)  # type: ignore[return-value]
    except (ImportError, NotImplementedError) as exc:
        logger.error(
            "document_parser_strategy_not_installed",
            requested=key,
            error=str(exc),
        )
        return NullParser(**kwargs)


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


def detect_parser(
    mime_type: str,
    file_ext: str,
) -> "DocumentParserPort | None":
    """Try each parser's :meth:`supports` — first non-null match wins.

    NullParser is skipped because ``supports`` always returns False there.
    """
    for provider in _REGISTRY:
        if provider == "null":
            continue
        try:
            parser = build_parser(provider)
        except (TypeError, ValueError, AttributeError):
            # registry is fail-soft: ``build_parser`` already swallows
            # ImportError/NotImplementedError; remaining surfaces are
            # constructor signature / config-shape mismatches.
            continue
        if isinstance(parser, NullParser):
            # build_parser fell back to NullParser (dep missing) — skip.
            continue
        if parser.supports(mime_type, file_ext):
            return parser
    return None


def _sniff_mime(content: bytes) -> str:
    """Best-effort mime from the bytes themselves — used when mime/ext are
    unreliable. A PDF fetched from a URL commonly arrives as
    ``application/octet-stream`` with no extension; without sniffing it would
    miss the registry and fall to the flat OCR path. Magic-number first
    (cheap, no dep), then the OOXML zip manifest, then Kreuzberg's detector
    for the long tail. Shares the OOXML peek with ``shared.mime_sniff`` so the
    registry and the OCR adapter resolve xlsx/docx/pptx identically."""
    if not content:
        return ""
    if content[:5] == b"%PDF-":
        return "application/pdf"
    # OOXML zip (xlsx / docx / pptx): read ``[Content_Types].xml`` so an
    # Office file arriving as octet-stream routes to its structured parser
    # instead of falling through to flat OCR.
    if content[:4] == b"PK\x03\x04":
        ooxml = sniff_real_mime(content, "", "")
        if "officedocument" in ooxml:
            return ooxml
    try:
        import kreuzberg

        detected = kreuzberg.detect_mime_type_from_bytes(content)
        if detected:
            return str(detected)
    except (ImportError, ValueError, TypeError, OSError):
        pass
    return ""


def detect_parser_robust(
    mime_type: str,
    file_ext: str,
    content: bytes | None = None,
    detector: "Callable[[str, str], DocumentParserPort | None] | None" = None,
) -> "DocumentParserPort | None":
    """``detect_parser`` + byte-sniff fallback (headless-BE one-flow rule).

    Every source — local bytes AND a URL whose body arrives with an empty /
    generic mime and no extension (e.g. a ``...?download`` PDF link) — must
    route to the correct structured parser, never silently down to flat OCR.
    Order: trust the declared ``(mime, ext)`` first; only sniff the body when
    nothing matched. Returns ``None`` only when even the sniffed type has no
    parser (a genuine OCR-fallback case, e.g. a scanned image).

    ``detector`` defaults to the module ``detect_parser``; callers that inject
    their own (e.g. DocumentService, for test isolation) pass it so BOTH the
    primary AND the sniffed lookup honour the injected detector.
    """
    _detect = detector or detect_parser
    parser = _detect(mime_type, file_ext)
    if parser is not None:
        return parser
    sniffed = _sniff_mime(content or b"")
    if sniffed and sniffed.lower() != (mime_type or "").lower():
        return _detect(sniffed, "")
    return None


__all__ = [
    "build_parser",
    "detect_parser",
    "detect_parser_robust",
    "list_providers",
]
