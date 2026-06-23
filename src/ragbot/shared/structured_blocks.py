"""Structured-markdown -> typed ``Block`` list (AdapChunk Layer 1 -> Layer 2 bridge).

The structured parsers (DOCX / XLSX / Google-Sheets-CSV / Kreuzberg PDF-PPTX-HTML)
all emit ONE canonical structured-markdown document: ``#`` heading hierarchy plus
``|`` pipe tables, with optional ``$$`` formula, ``![alt](url)`` image and triple-
backtick code fences. This module turns that markdown back into the typed
``domain.entities.document.Block`` stream the Block pipeline (Layer 2 context
buffer -> Layer 3 profile -> Layer 6 atomic-aware chunking) consumes, so the
``is_atomic`` provenance survives end-to-end instead of being re-detected from
flattened prose downstream.

Type mapping (domain-neutral, shape-only — no brand/industry vocabulary):
    ``#`` heading line          -> HEADING (atomic)
    ``|...|`` pipe-table run     -> TABLE   (atomic)
    ``$$...$$`` / ``$...$`` line -> FORMULA (atomic)
    ``![alt](url)`` line         -> IMAGE   (atomic)
    triple-backtick code fence   -> CODE    (atomic)
    everything else (prose)      -> TEXT    (non-atomic)

Classification reuses :func:`ragbot.shared.chunking.blocks._split_into_blocks_with_atomic`
(the single source of truth for table/formula/image/code detection) and layers
markdown ``#`` heading splitting on top, so there is exactly one detector to keep
in sync.
"""

from __future__ import annotations

from ragbot.domain.entities.document import Block
from ragbot.shared.chunking.blocks import _split_into_blocks_with_atomic
from ragbot.shared.types import BlockType

# Lower-case block-type vocabulary produced by ``_split_into_blocks_with_atomic``
# mapped onto the uppercase domain ``BlockType`` Literal. ``text`` is handled
# separately because a TEXT run may carry markdown ``#`` heading lines that must
# be split out into their own atomic HEADING blocks.
_TYPE_MAP: dict[str, BlockType] = {
    "table": "TABLE",
    "formula": "FORMULA",
    "image": "IMAGE",
    "code": "CODE",
}
# Atomic block types — preserved whole by Layer-6 ``smart_chunk_atomic``.
# HEADING is atomic so a section title is never cut from / merged into prose.
_ATOMIC_TYPES: frozenset[BlockType] = frozenset(
    {"HEADING", "TABLE", "FORMULA", "IMAGE", "CODE"},
)
# Markdown ATX headings nest from ``#`` (h1) to ``######`` (h6) per CommonMark.
_MAX_ATX_HEADING_DEPTH: int = 6


def _is_heading_line(line: str) -> bool:
    """True for a markdown ATX heading line (``#`` .. ``######`` + text)."""
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return False
    hashes = len(stripped) - len(stripped.lstrip("#"))
    return (
        1 <= hashes <= _MAX_ATX_HEADING_DEPTH
        and stripped[hashes:hashes + 1] in {" ", "\t"}
    )


def _split_text_run_on_headings(content: str) -> list[Block]:
    """Split a TEXT run into HEADING (atomic) + TEXT (prose) blocks.

    The structured parsers emit ``# Title`` heading lines inside prose runs.
    Each heading line becomes its own atomic HEADING block; the prose lines
    between headings stay as non-atomic TEXT blocks.
    """
    out: list[Block] = []
    buf: list[str] = []

    def _flush_text() -> None:
        body = "\n".join(buf).strip()
        if body:
            out.append(Block(type="TEXT", content=body, is_atomic=False))
        buf.clear()

    for line in content.split("\n"):
        if _is_heading_line(line):
            _flush_text()
            out.append(Block(type="HEADING", content=line.strip(), is_atomic=True))
        else:
            buf.append(line)
    _flush_text()
    return out


def markdown_to_blocks(markdown: str) -> list[Block]:
    """Convert structured markdown into a typed ``Block`` list.

    @param markdown: canonical structured-markdown emitted by a parser
    @return: typed ``Block`` stream — HEADING/TABLE/FORMULA/IMAGE/CODE atomic,
        TEXT non-atomic; empty list for blank input.
    """
    if not markdown or not markdown.strip():
        return []

    blocks: list[Block] = []
    for raw_type, content in _split_into_blocks_with_atomic(markdown):
        mapped = _TYPE_MAP.get(raw_type)
        if mapped is not None:
            blocks.append(
                Block(type=mapped, content=content, is_atomic=mapped in _ATOMIC_TYPES),
            )
            continue
        # ``text`` run — may contain markdown headings to split out.
        blocks.extend(_split_text_run_on_headings(content))
    return blocks


__all__ = ["markdown_to_blocks"]
