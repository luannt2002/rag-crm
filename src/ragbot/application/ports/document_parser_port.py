"""Document Parser Protocol — Strategy Pattern for ingestion.

Strategy port for swap-able document parsers (xlsx, sheets, future PDF/DOCX).
Default implementation is :class:`NullParser` — operator opt-in any provider
via ``system_config.document_parser_provider``.

Caller contract:
    parser = build_parser(provider="excel_openpyxl")
    if parser.supports(mime_type, file_ext):
        chunks = await parser.parse(content, file_name="...")

Each parser yields a list of ``{"content": str, "metadata": dict}`` so the
ingestion pipeline can normalise output regardless of source format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ragbot.domain.entities.document import Block


@runtime_checkable
class DocumentParserPort(Protocol):
    """Strategy interface for document parsers."""

    def supports(self, mime_type: str, file_ext: str) -> bool:
        """Return True if this parser handles the given MIME / extension."""
        ...

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        """Parse raw bytes into a list of chunks.

        @return: ``[{"content": str, "metadata": dict}, ...]``
        """
        ...

    def get_provider_name(self) -> str:
        """Return the registry key (e.g. ``"null"``, ``"excel_openpyxl"``)."""
        ...


@runtime_checkable
class StructuredParserPort(Protocol):
    """Optional, additive extension for structure-aware parsers.

    A parser implementing this surface emits a typed ``Block`` stream alongside
    its flat-markdown ``parse`` output so the Block pipeline (Layer-2 context
    buffer -> Layer-3 profile -> Layer-6 atomic-aware chunking) runs on real
    atomic blocks instead of re-detecting structure from flattened prose. The
    worker probes ``isinstance(parser, StructuredParserPort)`` and threads the
    result onto ``DocumentService.ingest(blocks=...)``. Parsers that do not
    implement it keep the dict-only ``parse`` contract unchanged.
    """

    async def parse_blocks(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[Block]:
        """Parse raw bytes into a typed ``Block`` list.

        @return: typed ``Block`` stream (HEADING/TABLE/FORMULA/IMAGE/CODE atomic,
            TEXT non-atomic); empty list when the source has no extractable text.
        """
        ...


__all__ = ["DocumentParserPort", "StructuredParserPort"]
