"""M18 — footer-below-table preservation tests (RAG-Anything mindset).

Bug report (2026-05-18): a markdown service table is followed by a short
explanatory paragraph (e.g. "Khuyến mãi: …"). When the splitter emits
the table and the footer as two separate blocks, downstream chunking
strands the footer in a different chunk than its table — retrieval for
the table topic loses the promotion / surcharge / disclaimer text.

The fix: in :func:`_split_into_blocks` (and the atomic variant), when a
TABLE block is immediately followed by a short non-heading TEXT block
(<= ``DEFAULT_TABLE_FOOTER_MAX_CHARS``), merge the two into ONE table
block so the chunker treats them as a single atomic semantic unit.

These tests guard the merge heuristic and its boundary cases.
"""

from __future__ import annotations

from ragbot.shared.chunking import (
    _is_heading_line,
    _merge_table_footer_blocks,
    _split_into_blocks,
    _split_into_blocks_with_atomic,
)
from ragbot.shared.constants import DEFAULT_TABLE_FOOTER_MAX_CHARS


# -----------------------------------------------------------------------------
# Golden case — bug report fixture
# -----------------------------------------------------------------------------

GOLDEN_TABLE_WITH_FOOTER = """\
| Dịch vụ | Giá |
|---------|-----|
| Massage | 500 |
| Facial  | 700 |

Khuyến mãi: giảm 20% cho mỗi dịch vụ thêm.
"""


def test_footer_below_table_merged_into_table_block() -> None:
    """Golden case from the bug report — footer must travel with the table."""
    blocks = _split_into_blocks(GOLDEN_TABLE_WITH_FOOTER)
    # After merge there must be exactly one block, typed ``table``, whose
    # content contains BOTH the pipe-table header AND the footer line.
    table_blocks = [b for b in blocks if b[0] == "table"]
    assert len(table_blocks) == 1, f"expected 1 table block, got {blocks}"
    table_content = table_blocks[0][1]
    assert "| Massage |" in table_content
    assert "Khuyến mãi" in table_content


# -----------------------------------------------------------------------------
# Boundary 1 — long footer must NOT merge
# -----------------------------------------------------------------------------


def test_long_footer_kept_separate() -> None:
    """A paragraph longer than the threshold is its own block."""
    long_paragraph = "A" * (DEFAULT_TABLE_FOOTER_MAX_CHARS + 50)
    text = (
        "| h1 | h2 |\n"
        "|----|----|\n"
        "| a  | b  |\n"
        "\n"
        f"{long_paragraph}\n"
    )
    blocks = _split_into_blocks(text)
    types = [b[0] for b in blocks]
    assert types == ["table", "text"], blocks


# -----------------------------------------------------------------------------
# Boundary 2 — heading must NOT merge into table
# -----------------------------------------------------------------------------


def test_heading_below_table_kept_separate() -> None:
    """A heading marks a new section — it must not be folded into the table."""
    text = (
        "| h1 | h2 |\n"
        "|----|----|\n"
        "| a  | b  |\n"
        "\n"
        "## Section 2\n"
    )
    blocks = _split_into_blocks(text)
    types = [b[0] for b in blocks]
    assert types == ["table", "text"], blocks
    # Heading content untouched.
    assert blocks[1][1].strip().startswith("## Section 2")


# -----------------------------------------------------------------------------
# Boundary 3 — vertical-bar shortcut heading detector
# -----------------------------------------------------------------------------


def test_heading_detector_recognises_markdown_h1_through_h3() -> None:
    """``# / ## / ###`` lines are headings; pipe-prefixed lines are not."""
    assert _is_heading_line("# Title") is True
    assert _is_heading_line("## Sub") is True
    assert _is_heading_line("### Sub-sub") is True
    assert _is_heading_line("####### deep") is True  # any depth
    assert _is_heading_line("Normal paragraph") is False
    assert _is_heading_line("| pipe | table | row |") is False
    assert _is_heading_line("") is False


# -----------------------------------------------------------------------------
# Boundary 4 — atomic-variant splitter must also honour the merge
# -----------------------------------------------------------------------------


def test_atomic_splitter_preserves_footer_below_table() -> None:
    """The atomic variant (formula/image-aware) must apply the same merge."""
    blocks = _split_into_blocks_with_atomic(GOLDEN_TABLE_WITH_FOOTER)
    table_blocks = [b for b in blocks if b[0] == "table"]
    assert len(table_blocks) == 1
    assert "Khuyến mãi" in table_blocks[0][1]


# -----------------------------------------------------------------------------
# Boundary 5 — no table → merge is a no-op
# -----------------------------------------------------------------------------


def test_no_table_no_merge() -> None:
    """A document without tables must come through untouched."""
    text = "Just a paragraph.\n\nAnother short paragraph.\n"
    blocks = _split_into_blocks(text)
    # 2 separate paragraphs collapse into 1 text block (existing behaviour
    # — there is no table boundary to flush).
    assert [b[0] for b in blocks] == ["text"], blocks
    assert "Just a paragraph" in blocks[0][1]
    assert "Another short paragraph" in blocks[0][1]


# -----------------------------------------------------------------------------
# Boundary 6 — disable flag bypasses the merge
# -----------------------------------------------------------------------------


def test_merge_helper_returns_input_when_disabled() -> None:
    """The merge helper accepts an ``enabled`` flag; False = identity pass."""
    blocks_in = [
        ("table", "| h |\n|---|\n| v |"),
        ("text", "Khuyến mãi: giảm 20%"),
    ]
    merged = _merge_table_footer_blocks(
        blocks_in, enabled=False, max_chars=DEFAULT_TABLE_FOOTER_MAX_CHARS,
    )
    # Disabled → no merge, list returned as-is (same length, same types).
    assert [b[0] for b in merged] == ["table", "text"]


def test_merge_helper_default_enabled() -> None:
    """When enabled with a generous threshold, the merge collapses the pair."""
    blocks_in = [
        ("table", "| h |\n|---|\n| v |"),
        ("text", "Khuyến mãi: giảm 20%"),
    ]
    merged = _merge_table_footer_blocks(
        blocks_in, enabled=True, max_chars=DEFAULT_TABLE_FOOTER_MAX_CHARS,
    )
    assert [b[0] for b in merged] == ["table"]
    assert "Khuyến mãi" in merged[0][1]
    # Pipe table header must still be there — merge appends, not replaces.
    assert "| h |" in merged[0][1]
