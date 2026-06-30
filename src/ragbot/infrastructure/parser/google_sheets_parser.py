"""GoogleSheetsParser — CSV-export ingestion for Google Sheets links.

Public-shared Google Sheets export to CSV via the
``/spreadsheets/d/{id}/export?format=csv&gid={N}`` endpoint. Upstream
(``DocumentService.ingest`` or the sync route) is responsible for
fetching the URL → ``raw_bytes``; this parser handles whatever CSV
bytes it receives. Emits ONE structured-markdown document (AdapChunk L1,
via ``rows_to_structured_markdown``) — multi-table + section-title aware —
the same canonical form as ``ExcelOpenpyxlParser`` and the Kreuzberg parser.

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


def _is_separator_row(s: str) -> bool:
    """A markdown table separator row: ``| --- | --- |`` (only dashes/colons)."""
    cells = [c.strip() for c in s.strip().strip("|").split("|")]
    return bool(cells) and all(bool(c) and set(c) <= {"-", ":"} for c in cells)


def _split_md_to_row_chunks(markdown: str) -> list[str]:
    """Split section-bound structured markdown into ATOMIC per-row chunks.

    Each emitted chunk = the nearest ``## section`` heading + the table header
    + its separator + EXACTLY ONE data row, so every data row carries its own
    column labels and section context. The LLM therefore never sees two rows
    packed in one chunk → no cross-row value mis-binding (the bot reading the
    stock/price/date of a neighbouring row). A header with no data rows emits
    nothing (no orphan header-only chunk). Non-table prose lines become their
    own chunk under the current section. Domain-neutral — shape only, no
    vocabulary.
    """
    out: list[str] = []
    section = ""
    header = ""
    sep = ""
    for raw in markdown.splitlines():
        s = raw.strip()
        if not s:
            header = ""  # blank line closes the current table
            sep = ""
            continue
        if s.startswith("##"):
            section = s
            header = ""
            sep = ""
            continue
        if s.startswith("|"):
            if _is_separator_row(s):
                sep = raw
                continue
            if not header:
                header = raw  # first pipe row of a table = its header
                continue
            out.append("\n".join(p for p in (section, header, sep, raw) if p.strip()))
            continue
        out.append("\n".join(p for p in (section, s) if p.strip()))
    return out


class GoogleSheetsParser:
    """Google Sheets parser — CSV-export bytes → ONE structured-markdown doc."""

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
        # ROW-AS-CHUNK: split the section-bound markdown into one atomic chunk
        # per data row (header + section bound into each) so the row-preserve
        # path stores each row independently. A multi-row blob let the chunker
        # pack several rows together → the LLM mis-bound a value to the wrong
        # row. Fallback to the whole doc if the split yields nothing (never
        # drop content). Stats extraction is unaffected — it runs on raw rows.
        row_chunks = _split_md_to_row_chunks(markdown) or [markdown]
        base_meta = {
            "sheet_name": file_name,
            "file_name": file_name,
            "parser": self.get_provider_name(),
            "format": "markdown",
            "section_headings": heading_lines,
        }
        return [{"content": c, "metadata": dict(base_meta)} for c in row_chunks]


__all__ = ["GoogleSheetsParser"]
