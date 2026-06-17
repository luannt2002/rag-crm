"""AdapChunk Layer 7 — TABLE narrator (rule-based, $0 cost).

Per debug doc Phan 3.8 narrate-then-embed pattern:
'TABLE Markdown -> linearize row-by-row -> cau mo ta. Luu original_content
trong metadata.'

Output is used as ``narrated_text`` for embedding; the original markdown
table stays in ``Chunk.original_content`` so the LLM at retrieval time
still sees the exact table for factual answers (HALLU=0 sacred).

This module is purely deterministic regex + string formatting — zero LLM
calls, sub-millisecond latency per table. It is the cost-free alternative
to :class:`ragbot.application.services.narrate_service.NarrateService`
for the TABLE block type; FORMULA / IMAGE blocks still need the LLM path.
"""
from __future__ import annotations

import re
from typing import Iterator

from ragbot.shared.constants import DEFAULT_TABLE_NARRATE_MAX_ROWS


def narrate_table(
    markdown_table: str,
    *,
    max_rows: int = DEFAULT_TABLE_NARRATE_MAX_ROWS,
) -> str:
    """Convert a markdown table into a row-by-row narrative.

    Example:
        Input::

            | Name | Price | Qty |
            |------|-------|-----|
            | A    | 10    | 5   |
            | B    | 20    | 3   |

        Output::

            "Table with 3 columns (Name, Price, Qty). Row 1: Name=A,
             Price=10, Qty=5. Row 2: Name=B, Price=20, Qty=3."

    Behaviour:
        - non-table / malformed input (no pipe-delimited lines parsed) is
          returned verbatim — caller can decide whether to embed raw or
          skip. This keeps the function safe to call unconditionally.
        - the body is truncated to ``max_rows`` rows; the tail count is
          appended as ``(... and N more rows)`` so downstream embeddings
          still encode the table's overall shape.
    """
    rows = list(_parse_markdown_table(markdown_table))
    if not rows:
        return markdown_table  # passthrough non-table content (incl. empty str)

    headers = rows[0]
    body = rows[1 : max_rows + 1]

    parts = [f"Table with {len(headers)} columns ({', '.join(headers)})."]
    for i, row in enumerate(body, 1):
        cells = ", ".join(f"{h}={v}" for h, v in zip(headers, row, strict=False))
        parts.append(f"Row {i}: {cells}.")

    remaining = len(rows) - 1 - max_rows
    if remaining > 0:
        parts.append(f"(... and {remaining} more rows)")

    return " ".join(parts)


def _parse_markdown_table(text: str) -> Iterator[list[str]]:
    """Yield rows of cells from a pipe-delimited markdown table.

    Skips the separator row (``|---|---|``) which markdown tables use
    between headers and body.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if _is_separator_row(line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]  # drop empty edges
        if cells:
            yield cells


def _is_separator_row(line: str) -> bool:
    """Detect ``|---|---|`` or ``|:---:|`` separator rows."""
    stripped = line.strip().strip("|").strip()
    parts = [p.strip() for p in stripped.split("|")]
    return all(re.fullmatch(r":?-+:?", p) for p in parts if p)


__all__ = ["narrate_table"]
