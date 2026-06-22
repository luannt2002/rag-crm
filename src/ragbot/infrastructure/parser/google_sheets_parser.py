"""GoogleSheetsParser — CSV-export ingestion for Google Sheets links.

Public-shared Google Sheets export to CSV via the
``/spreadsheets/d/{id}/export?format=csv&gid={N}`` endpoint. Upstream
(``DocumentService.ingest`` or the sync route) is responsible for
fetching the URL → ``raw_bytes``; this parser handles whatever CSV
bytes it receives. Same row-as-chunk shape as ``ExcelOpenpyxlParser``
so the downstream chunker keeps the 1-row → 1-chunk semantics.

Private (OAuth-only) sheets are out of scope here — they need an
authenticated fetch handled at the boundary, then the bytes flow
through this parser unchanged.
"""

from __future__ import annotations

import csv
import io

import structlog

from ragbot.shared.tabular_markdown import rows_to_structured_markdown

logger = structlog.get_logger(__name__)


_SHEETS_MIME: str = "application/vnd.google-apps.spreadsheet"
_CSV_MIME: str = "text/csv"
_CSV_EXT: str = ".csv"


def _decode_csv(content: bytes) -> str:
    if not content:
        return ""
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1", errors="replace")


class GoogleSheetsParser:
    """Google Sheets parser — CSV-export bytes → row-as-chunk dicts."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "google_sheets"

    def supports(self, mime_type: str, file_ext: str) -> bool:
        mt = (mime_type or "").strip().lower()
        ext = (file_ext or "").strip().lower()
        return mt == _SHEETS_MIME or mt == _CSV_MIME or ext == _CSV_EXT

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        """Return ONE structured-markdown document (AdapChunk L1).

        A sheet routinely stacks MANY sub-tables, each with its own section title
        and local header. The old row-1-as-global-header flat output mislabelled
        every row of every sub-table after the first. Convert the whole sheet to
        section-bound structured markdown (``## <title>`` + ``| table |``) so the
        downstream chunker/extractor can bind each row to BOTH its column header
        and its service/section — same canonical form as the Kreuzberg markdown
        parser. Domain-neutral: structure derives from the workbook shape, not
        any hardcoded label.
        """
        text = _decode_csv(content)
        if not text.strip():
            return []

        rows = list(csv.reader(io.StringIO(text)))
        markdown = rows_to_structured_markdown(rows)
        if not markdown.strip():
            return []

        heading_lines = sum(
            1 for ln in markdown.splitlines() if ln.lstrip().startswith("##")
        )
        logger.info(
            "google_sheets_csv_parsed",
            file_name=file_name,
            bytes=len(content),
            markdown_chars=len(markdown),
            section_headings=heading_lines,
        )
        return [
            {
                "content": markdown,
                "metadata": {
                    "sheet_name": file_name,
                    "file_name": file_name,
                    "parser": self.get_provider_name(),
                    "format": "markdown",
                    "section_headings": heading_lines,
                },
            }
        ]


__all__ = ["GoogleSheetsParser"]
