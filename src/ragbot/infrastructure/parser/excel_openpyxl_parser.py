"""ExcelOpenpyxlParser — REAL strategy for ``.xlsx`` ingestion.

Uses ``openpyxl`` (Apache 2.0, pure-Python) to walk every sheet and emit one
chunk per non-empty row. Header detection: row 1 of each sheet is treated as
the header row; subsequent rows are rendered as ``"col1: val1 | col2: val2"``
to preserve column semantics for the embedding model.

Domain-neutral: NO hardcoded sheet / column names — every label flows from
the workbook. ``openpyxl`` is an optional dep; init raises ``ImportError``
when missing so the registry's fail-soft path falls back to NullParser and
operators see the install hint in logs.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import structlog

from ragbot.shared.constants import DEFAULT_EXCEL_HEADER_ROW_INDEX

logger = structlog.get_logger(__name__)


_XLSX_MIME: str = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_XLSX_EXT: str = ".xlsx"


def _openpyxl_available() -> bool:
    try:
        import openpyxl  # noqa: F401

        return True
    except ImportError:
        return False


class ExcelOpenpyxlParser:
    """Excel ``.xlsx`` parser — header-aware row-as-chunk."""

    def __init__(self, **_: object) -> None:
        if not _openpyxl_available():
            raise ImportError(
                "openpyxl is not installed; add to pyproject or set "
                "system_config.document_parser_provider='null'."
            )

    @staticmethod
    def get_provider_name() -> str:
        return "excel_openpyxl"

    def supports(self, mime_type: str, file_ext: str) -> bool:
        return (
            (mime_type or "").strip().lower() == _XLSX_MIME
            or (file_ext or "").strip().lower() == _XLSX_EXT
        )

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        # Local import — module-level import would crash on systems without
        # openpyxl, defeating the registry's fail-soft fallback.
        from openpyxl import load_workbook

        wb = load_workbook(filename=BytesIO(content), data_only=True, read_only=True)
        chunks: list[dict[str, Any]] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_iter = ws.iter_rows(values_only=True)

            try:
                header_row = next(rows_iter)
            except StopIteration:
                continue  # empty sheet

            headers = [str(h).strip() if h is not None else "" for h in header_row]

            for row_idx, row in enumerate(
                rows_iter,
                start=DEFAULT_EXCEL_HEADER_ROW_INDEX + 1,
            ):
                cells = [c for c in row if c is not None and str(c).strip()]
                if not cells:
                    continue

                pairs = []
                for col_idx, val in enumerate(row):
                    if val is None or not str(val).strip():
                        continue
                    label = (
                        headers[col_idx]
                        if col_idx < len(headers) and headers[col_idx]
                        else f"col{col_idx + 1}"
                    )
                    pairs.append(f"{label}: {val}")

                if not pairs:
                    continue

                chunks.append({
                    "content": " | ".join(pairs),
                    "metadata": {
                        "sheet_name": sheet_name,
                        "row_index": row_idx,
                        "file_name": file_name,
                        "parser": self.get_provider_name(),
                    },
                })

        wb.close()
        logger.info(
            "excel_openpyxl_parsed",
            file_name=file_name,
            sheets=len(wb.sheetnames),
            chunks=len(chunks),
        )
        return chunks


__all__ = ["ExcelOpenpyxlParser"]
