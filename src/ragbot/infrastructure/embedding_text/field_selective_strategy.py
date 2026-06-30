"""Field-selective embedding-text strategy — drop keyword/alias FLOOD cells
from the dense-embedding text of a row-as-chunk table.

A spreadsheet row can carry one cell that is a huge list of format variations
(aliases / keywords / synonyms — dozens of short tokens). Embedding the whole
row lets that cell dominate the vector (often the large majority of tokens) so
the dense encoder can no longer tell one record from another → spec-based
retrieval misses (dilution). This strategy removes such cells from the text the
encoder sees. The full row is UNCHANGED in ``content`` (shown to the LLM) and in
``content_segmented`` (BM25 / keyword channel), so every alias stays exact-match
searchable — only the dense vector is cleaned. Dense + BM25 are fused (RRF), so
recall is preserved while the vector becomes discriminative.

Pure function, shape-only, DOMAIN-NEUTRAL: a cell is a "flood" cell purely by its
SHAPE (many short separator-delimited tokens, long total), never by column name,
header text, language or bot identity. Non-table chunks and rows without a flood
cell degrade BYTE-IDENTICALLY to ``prefix_plus_raw``.
"""
from __future__ import annotations

import re

from ragbot.shared.constants import (
    DEFAULT_EMBED_FLOOD_CELL_MIN_CHARS,
    DEFAULT_EMBED_FLOOD_CELL_MIN_PARTS,
    EMBEDDING_TEXT_STRATEGY_FIELD_SELECTIVE,
)

STRATEGY_NAME = EMBEDDING_TEXT_STRATEGY_FIELD_SELECTIVE

# Separators that delimit list-like keyword/alias tokens INSIDE one cell.
_LIST_SEP_RE = re.compile(r"[,;]")


def _is_flood_cell(cell: str) -> bool:
    """True when *cell* is a long list of many short separator-delimited tokens
    — the alias/keyword swamp. Shape-only; no vocabulary, no column awareness."""
    c = cell.strip()
    if len(c) < DEFAULT_EMBED_FLOOD_CELL_MIN_CHARS:
        return False
    parts = [p for p in _LIST_SEP_RE.split(c) if p.strip()]
    return len(parts) >= DEFAULT_EMBED_FLOOD_CELL_MIN_PARTS


def _strip_flood_cells(text: str) -> str:
    """Blank every flood cell inside markdown table rows, keeping the row's
    structure + every other (discriminative) cell. Lines that are not pipe table
    rows are returned unchanged. Returns the input unchanged when nothing was
    stripped (byte-identical happy path)."""
    changed = False
    out: list[str] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            out.append(line)
            continue
        cells = line.split("|")
        new_cells: list[str] = []
        for c in cells:
            if _is_flood_cell(c):
                new_cells.append(" ")  # keep column alignment; drop the swamp
                changed = True
            else:
                new_cells.append(c)
        out.append("|".join(new_cells))
    return "\n".join(out) if changed else text


class FieldSelectiveStrategy:
    """Embed ``{prefix}\\n\\n{raw}`` but with keyword/alias FLOOD cells stripped
    from ``raw`` first. Degrades to plain prefix+raw when no flood cell exists."""

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    def build(self, *, raw_chunk: str, enriched_prefix: str | None) -> str:
        cleaned = _strip_flood_cells(raw_chunk)
        prefix = (enriched_prefix or "").strip()
        if not prefix:
            return cleaned
        return f"{prefix}\n\n{cleaned}"


__all__ = ["FieldSelectiveStrategy", "STRATEGY_NAME"]
