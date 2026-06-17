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

from ragbot.infrastructure.parser.docx_parser import DocxParser
from ragbot.infrastructure.parser.excel_openpyxl_parser import ExcelOpenpyxlParser
from ragbot.infrastructure.parser.google_sheets_parser import GoogleSheetsParser
from ragbot.infrastructure.parser.markdown_parser import MarkdownParser
from ragbot.infrastructure.parser.null_parser import NullParser
from ragbot.infrastructure.parser.pdf_parser import PdfParser

if TYPE_CHECKING:
    from ragbot.application.ports.document_parser_port import DocumentParserPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "null": NullParser,
    "excel_openpyxl": ExcelOpenpyxlParser,
    "google_sheets": GoogleSheetsParser,
    "pdf": PdfParser,
    "docx": DocxParser,
    "markdown": MarkdownParser,
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


__all__ = ["build_parser", "detect_parser", "list_providers"]
