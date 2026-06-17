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
import unicodedata
from typing import Any

import structlog

from ragbot.shared.constants import CUSTOMER_CONTEXT_COLUMN_NAMES

logger = structlog.get_logger(__name__)


_SHEETS_MIME: str = "application/vnd.google-apps.spreadsheet"
_CSV_MIME: str = "text/csv"
_CSV_EXT: str = ".csv"


def _norm_label(s: str) -> str:
    """Normalise a header label for context-column matching.

    Accent-strip + lowercase so 'Mô tả' / 'mo ta' / 'MO TA' all match.
    """
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.strip().lower()


_CONTEXT_LABELS_NORM = {_norm_label(name) for name in CUSTOMER_CONTEXT_COLUMN_NAMES}


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
        """Return one chunk per data row (header preserved as labels)."""
        text = _decode_csv(content)
        if not text.strip():
            return []

        reader = csv.reader(io.StringIO(text))
        try:
            header_row = next(reader)
        except StopIteration:
            return []

        headers = [(h or "").strip() for h in header_row]
        # Phase 4.5: detect a customer-supplied "Topic" / "Mô tả" column.
        # If present, lift each row's value out of the chunk body and into
        # metadata.enriched_prefix (Anthropic CR vocabulary) so retrieval
        # boosts on customer-curated topic phrasing without LLM auto-extract.
        context_col_idx: int | None = None
        for idx, h in enumerate(headers):
            if _norm_label(h) in _CONTEXT_LABELS_NORM:
                context_col_idx = idx
                break

        chunks: list[dict[str, Any]] = []

        for row_idx, row in enumerate(reader, start=2):
            cells = [(c or "").strip() for c in row]
            if not any(cells):
                continue

            pairs: list[str] = []
            customer_context: str = ""
            for col_idx, val in enumerate(cells):
                if not val:
                    continue
                if col_idx == context_col_idx:
                    customer_context = val
                    continue  # don't echo Topic into the body
                label = (
                    headers[col_idx]
                    if col_idx < len(headers) and headers[col_idx]
                    else f"col{col_idx + 1}"
                )
                pairs.append(f"{label}: {val}")

            if not pairs:
                continue

            metadata: dict[str, Any] = {
                "sheet_name": file_name,
                "row_index": row_idx,
                "file_name": file_name,
                "parser": self.get_provider_name(),
            }
            if customer_context:
                metadata["enriched_prefix"] = customer_context
                metadata["enriched_prefix_source"] = "customer_topic_column"

            chunks.append({
                "content": " | ".join(pairs),
                "metadata": metadata,
            })

        logger.info(
            "google_sheets_csv_parsed",
            file_name=file_name,
            chunks=len(chunks),
            bytes=len(content),
        )
        return chunks


__all__ = ["GoogleSheetsParser"]
