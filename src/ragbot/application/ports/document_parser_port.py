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

from typing import Protocol, runtime_checkable


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


__all__ = ["DocumentParserPort"]
