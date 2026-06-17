"""Block splitting + atomic-block protection (TABLE/FORMULA/IMAGE never cut).

Extracted from the chunking god-file: splits cleaned text into typed blocks and
shields atomic blocks (tables, formulas, images) from being cut mid-structure.
Pure logic; re-exported by chunking/__init__ so existing imports stay valid."""
from __future__ import annotations

import re
from typing import Any

import structlog

from ragbot.shared.bootstrap_config import get_boot_config  # noqa: F401
from ragbot.shared.constants import *  # noqa: F401,F403
from ragbot.shared.chunking.vn_structural import *  # noqa: F401,F403
from ragbot.shared.chunking.analyze import *  # noqa: F401,F403

logger = structlog.get_logger(__name__)


def _merge_table_footer_blocks(
    blocks: list[tuple[str, str]],
    *,
    enabled: bool = DEFAULT_TABLE_FOOTER_PRESERVE_ENABLED,
    max_chars: int = DEFAULT_TABLE_FOOTER_MAX_CHARS,
) -> list[tuple[str, str]]:
    """Merge a short non-heading TEXT block trailing a TABLE block back in.

    RAG-Anything M18 mindset: a table plus its explanatory footer
    ("Khuyến mãi: …", "Đơn giá đã bao gồm VAT", …) form ONE semantic
    unit. Splitting them across chunks loses the disclaimer / promo on
    table-topic retrieval.

    Heuristic:

    * ``enabled is False`` → return input untouched (per-bot opt-out).
    * Iterate pair-wise. When ``(type=table, type=text)`` and the text
      block is at most ``max_chars`` long AND does NOT begin with a
      markdown heading line, fold the text body into the table body
      and drop the standalone text block.
    * Heading-led footers, long paragraphs, and atomic non-text types
      (``formula`` / ``image`` / ``code``) are never folded.
    """
    if not enabled or not blocks:
        return blocks

    merged: list[tuple[str, str]] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if (
            cur[0] == "table"
            and nxt is not None
            and nxt[0] == "text"
            and len(nxt[1]) <= max_chars
            and not _is_heading_line(nxt[1].splitlines()[0] if nxt[1] else "")
        ):
            merged.append((cur[0], f"{cur[1]}\n\n{nxt[1]}"))
            i += 2
            continue
        merged.append(cur)
        i += 1
    return merged


def _split_into_blocks(text: str) -> list[tuple[str, str]]:
    """Tách document thành blocks: ('text', content) hoặc ('table', content).

    Table block = nhóm liên tiếp các dòng table.
    Text block = phần còn lại.

    M18 footer-below-table preserve runs as a post-pass — see
    :func:`_merge_table_footer_blocks` for the merge heuristic.
    """
    lines = text.split("\n")
    blocks: list[tuple[str, str]] = []
    current_type = "text"
    current_lines: list[str] = []
    in_code_fence = False

    for line in lines:
        # Track code fences (``` blocks are also atomic)
        if line.strip().startswith("```"):
            in_code_fence = not in_code_fence
            current_lines.append(line)
            continue

        if in_code_fence:
            current_lines.append(line)
            continue

        is_table = _is_table_line(line)

        if is_table and current_type == "text":
            # Flush text block
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    blocks.append(("text", content))
            current_lines = [line]
            current_type = "table"
        elif not is_table and current_type == "table":
            # Flush table block
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    blocks.append(("table", content))
            current_lines = [line]
            current_type = "text"
        else:
            current_lines.append(line)

    # Flush last block
    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            blocks.append((current_type, content))

    return _merge_table_footer_blocks(blocks)


# ---------------------------------------------------------------------------
# Atomic FORMULA / IMAGE / CODE protection
# ---------------------------------------------------------------------------
#
# Atomic Block Protection (TABLE / FORMULA / IMAGE / CODE)
# Inspired by:
#   - RAG-Anything HKUDS (06/2025): "Tables treated as atomic semantic units"
#     https://arxiv.org/abs/2503.13838
#   - AdapChunk Layer 2 internal blueprint — "vùng cấm cắt" pattern
#   - Goldman Sachs industrial RAG pattern (formula preservation)
#
# Atomic block types:
#   table   — markdown pipe-table or CSV-aligned rows
#   formula — LaTeX block ($$…$$) or display-math line
#   image   — markdown ![alt](url) reference
#   code    — triple-backtick fenced block
#
# Protection rule: when ``formula_image_atomic_protect_enabled`` is on,
# no chunking strategy may cut across an atomic block. FORMULA and IMAGE
# blocks stay whole regardless of size (splitting a formula or image
# caption breaks semantic atomicity). TABLE / CODE may be split on row
# / fenced-line boundary when oversized (existing behaviour preserved).

_ATOMIC_BLOCK_TYPES: frozenset[str] = frozenset({"table", "formula", "image", "code"})

# Single-line display-math: ``$$ … $$`` on one line. ``re.DOTALL`` is
# not needed because the body must NOT contain a newline — multi-line
# ``$$ … $$`` blocks are caught by the ``$$``-toggle path in
# :func:`_split_into_blocks_with_atomic`.
_FORMULA_BLOCK_ONELINE_RE = re.compile(r"^\s*\$\$[^\n]+\$\$\s*$")
# Standalone inline-math line: ``$…$`` ALONE on its line. Inline ``$x$``
# embedded in flowing prose is intentionally NOT flagged (splitting
# prose around inline math breaks the sentence).
_FORMULA_INLINE_BLOCK_RE = re.compile(r"^\s*\$[^\$\n]+\$\s*$")
# Markdown image reference. Image is atomic — caption (alt text) +
# source URL must travel together with surrounding context buffer.
_IMAGE_LINE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def _is_formula_line(line: str) -> bool:
    """Detect a standalone formula line (``$$…$$`` one-line OR ``$…$`` block).

    Multi-line ``$$…$$`` formulas are caught at fence-toggle level by
    :func:`_split_into_blocks_with_atomic`. This helper only flags
    single-line forms.
    """
    stripped = line.strip()
    if not stripped:
        return False
    # Single-line ``$$ x = 1 $$`` block — regex ensures non-empty body.
    if _FORMULA_BLOCK_ONELINE_RE.match(stripped):
        return True
    # Standalone inline-math line (``$f(x) = …$`` alone on its line).
    return bool(_FORMULA_INLINE_BLOCK_RE.match(stripped))


def _is_image_line(line: str) -> bool:
    """Detect a markdown image reference line (``![alt](url)``)."""
    return bool(_IMAGE_LINE_RE.search(line))


def _split_into_blocks_with_atomic(text: str) -> list[tuple[str, str]]:
    """Tách document thành blocks với atomic-type detection.

    Block types: ``text`` | ``table`` | ``formula`` | ``image`` | ``code``.
    Atomic types (``_ATOMIC_BLOCK_TYPES``) MUST be preserved whole by
    every chunking strategy — cuts across atomic boundary are forbidden.

    Detection order on each non-empty line:
        1. Triple-backtick fence → toggle ``code`` state.
        2. Inside ``$$…$$`` multi-line formula → continue ``formula``.
        3. Standalone formula line (``$$x$$`` / ``$x$`` alone) → ``formula``.
        4. Image line (``![alt](url)``) → ``image``.
        5. Table line (``|…|``, TSV, CSV-aligned) → ``table``.
        6. Otherwise → ``text``.

    Atomic blocks of different types never merge — a formula immediately
    followed by an image yields two separate atomic blocks.
    """
    lines = text.split("\n")
    blocks: list[tuple[str, str]] = []
    current_type = "text"
    current_lines: list[str] = []
    in_code_fence = False
    in_formula_block = False  # inside multi-line ``$$…$$``

    def _flush() -> None:
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            blocks.append((current_type, content))

    for line in lines:
        stripped = line.strip()

        # 1. Triple-backtick code fence — toggle and consume.
        if stripped.startswith("```"):
            if not in_code_fence:
                # Opening fence — flush previous block, start code.
                _flush()
                current_lines = [line]
                current_type = "code"
                in_code_fence = True
            else:
                # Closing fence — finalise code block.
                current_lines.append(line)
                in_code_fence = False
                _flush()
                current_lines = []
                current_type = "text"
            continue
        if in_code_fence:
            current_lines.append(line)
            continue

        # 2. Multi-line ``$$…$$`` formula — handle ``$$`` line toggles.
        if stripped == "$$":
            if not in_formula_block:
                _flush()
                current_lines = [line]
                current_type = "formula"
                in_formula_block = True
            else:
                current_lines.append(line)
                in_formula_block = False
                _flush()
                current_lines = []
                current_type = "text"
            continue
        if in_formula_block:
            current_lines.append(line)
            continue

        # 3-6. Classify the line via single-line heuristics.
        if _is_formula_line(line):
            line_type = "formula"
        elif _is_image_line(line):
            line_type = "image"
        elif _is_table_line(line):
            line_type = "table"
        else:
            line_type = "text"

        if line_type != current_type:
            _flush()
            current_lines = [line]
            current_type = line_type
        else:
            current_lines.append(line)

    _flush()
    # M18 — fold short non-heading text trailing a table into the table block.
    return _merge_table_footer_blocks(blocks)


def _is_atomic_block_type(block_type: str) -> bool:
    """Return True when ``block_type`` must be preserved whole.

    Used by every chunking strategy entry-point to bypass the splitter
    when the atomic-protect feature flag is enabled.
    """
    return block_type in _ATOMIC_BLOCK_TYPES


def _atomic_protect_enabled() -> bool:
    """Read the ``formula_image_atomic_protect_enabled`` feature flag.

    Resolution chain (per ``shared/bootstrap_config.py``):
        1. In-process TTL cache (30s) populated from
           ``system_config.formula_image_atomic_protect_enabled``.
        2. ``DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED`` constant.

    DB-read failure or missing row → constant default (OFF) — chunking
    never crashes because of a Postgres blip.
    """
    # Lazy import — keeps shared/chunking.py free of psycopg2 import at
    # module level (chunking is also called from non-DB worker paths
    # such as unit tests on synthetic fixtures).
    from ragbot.shared.bootstrap_config import get_boot_config

    return bool(
        get_boot_config(
            "formula_image_atomic_protect_enabled",
            DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED,
        ),
    )


# ---------------------------------------------------------------------------
# Strategy: Table-CSV (row-as-chunk for column-aligned tabular data)
# ---------------------------------------------------------------------------


__all__ = [
    "_ATOMIC_BLOCK_TYPES",
    "_merge_table_footer_blocks",
    "_split_into_blocks",
    "_FORMULA_BLOCK_ONELINE_RE",
    "_FORMULA_INLINE_BLOCK_RE",
    "_IMAGE_LINE_RE",
    "_is_formula_line",
    "_is_image_line",
    "_split_into_blocks_with_atomic",
    "_is_atomic_block_type",
    "_atomic_protect_enabled",
]
