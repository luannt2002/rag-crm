"""AdapChunk Layer 3 (Phần 6.4) — Document Profile full-fields tests.

Wave D1 — verifies the 4 fields newly populated by ``analyze_document()``:
``formula_count`` / ``image_count`` / ``code_block_count`` / ``heading_ratio``.
Also covers the ``analyze_document_blocks()`` overload which consumes a
parsed Block list directly (no text re-flatten).
"""
from __future__ import annotations

import pytest

from ragbot.domain.entities.document import Block
from ragbot.shared.chunking import analyze_document, analyze_document_blocks


# ── analyze_document (text-based extractors) ────────────────────────────


def test_formula_count_inline_and_display() -> None:
    """Both inline ``$...$`` and display ``\\[...\\]`` math count."""
    text = (
        "# Title\n\n"
        "Inline math $E=mc^2$ and another $a+b$.\n"
        "Display math: \\[ \\sum_{i=1}^n i \\]\n"
        "Parens: \\( x^2 \\).\n"
    )
    profile = analyze_document(text)
    # 2 inline + 1 \[..\] + 1 \(..\) = 4
    assert profile["formula_count"] == 4


def test_image_count_markdown_and_html() -> None:
    """Both ``![alt](url)`` and ``<img ...>`` count."""
    text = (
        "# Doc\n\n"
        "![cat](cat.png) here.\n"
        "Inline html: <img src='dog.png'/> and ![bird](bird.jpg)\n"
    )
    profile = analyze_document(text)
    assert profile["image_count"] == 3


def test_code_block_count_pairs_fences() -> None:
    """Each open/close pair is one block; unterminated pair ignored."""
    text = (
        "# Doc\n\n"
        "```python\nprint(1)\n```\n\n"
        "Some text.\n\n"
        "```bash\nls -la\n```\n"
    )
    profile = analyze_document(text)
    assert profile["code_block_count"] == 2


def test_code_block_count_zero_when_no_fences() -> None:
    text = "# Just headings\n\n## Sub\n\nPlain text only.\n"
    profile = analyze_document(text)
    assert profile["code_block_count"] == 0


def test_heading_ratio_high_when_mostly_headings() -> None:
    """Doc with many headings, few non-heading blocks → ratio close to 1."""
    text = "# A\n## B\n### C\n# D\n## E\n"
    profile = analyze_document(text)
    # 5 headings, no tables/formulas/images/code → ratio = 5/5 = 1.0
    assert profile["heading_ratio"] == 1.0


def test_heading_ratio_drops_with_mixed_content() -> None:
    text = (
        "# One\n\n"
        "Some text.\n"
        "$x=1$\n"
        "![img](a.png)\n"
        "```\ncode\n```\n"
    )
    profile = analyze_document(text)
    # 1 heading vs 1 formula + 1 image + 1 code = ratio = 1/4 = 0.25
    assert profile["heading_ratio"] == pytest.approx(0.25, abs=0.01)


def test_all_four_fields_present_and_typed() -> None:
    """Regression guard — keys exist with correct primitive types."""
    profile = analyze_document("# H\n\n$x$\n![i](u)\n```\nk\n```\n")
    for key in ("formula_count", "image_count", "code_block_count"):
        assert isinstance(profile[key], int)
    assert isinstance(profile["heading_ratio"], float)
    assert isinstance(profile["total_blocks_estimated"], int)


def test_baseline_fields_unchanged() -> None:
    """Existing callers must keep working — KEEP existing keys."""
    profile = analyze_document("# H\n\n## Sub\n\nText.\n")
    for baseline_key in (
        "heading_counts",
        "total_headings",
        "table_count",
        "avg_text_length",
        "mixed_content_score",
        "total_words",
        "has_toc",
        "is_csv_format",
        "vn_hierarchical_markers",
    ):
        assert baseline_key in profile, f"baseline key dropped: {baseline_key}"


# ── analyze_document_blocks (Block-list overload) ───────────────────────


def _b(block_type: str, content: str) -> Block:
    return Block(type=block_type, content=content, is_atomic=False)


def test_analyze_blocks_counts_by_type() -> None:
    blocks = [
        _b("HEADING", "# Title"),
        _b("HEADING", "## Sub"),
        _b("TEXT", "First paragraph of text content."),
        _b("TEXT", "Second paragraph."),
        _b("TABLE", "col1 | col2\nv1 | v2\nv3 | v4"),
        _b("FORMULA", "E=mc^2"),
        _b("IMAGE", "alt: cat"),
        _b("CODE", "print(1)"),
    ]
    profile = analyze_document_blocks(blocks)

    assert profile["total_headings"] == 2
    assert profile["table_count"] == 1
    assert profile["formula_count"] == 1
    assert profile["image_count"] == 1
    assert profile["code_block_count"] == 1
    assert profile["total_blocks"] == len(blocks)


def test_analyze_blocks_heading_counts_breakdown() -> None:
    blocks = [
        _b("HEADING", "# H1"),
        _b("HEADING", "## H2a"),
        _b("HEADING", "## H2b"),
        _b("HEADING", "### H3"),
    ]
    profile = analyze_document_blocks(blocks)
    assert profile["heading_counts"] == {"h1": 1, "h2": 2, "h3": 1}


def test_analyze_blocks_table_avg_rows() -> None:
    blocks = [
        _b("TABLE", "h1\nr1\nr2"),  # 2 newlines
        _b("TABLE", "h1\nr1\nr2\nr3"),  # 3 newlines
    ]
    profile = analyze_document_blocks(blocks)
    # (2 + 3) / 2 = 2.5
    assert profile["table_avg_rows"] == 2.5


def test_analyze_blocks_mixed_content_score() -> None:
    blocks = [
        _b("TEXT", "a"),
        _b("TEXT", "b"),
        _b("TABLE", "x"),
        _b("CODE", "y"),
    ]
    profile = analyze_document_blocks(blocks)
    # mixed = (4 - 2 text) / 4 = 0.5
    assert profile["mixed_content_score"] == 0.5


def test_analyze_blocks_empty_does_not_divide_by_zero() -> None:
    profile = analyze_document_blocks([])
    assert profile["total_blocks"] == 0
    assert profile["heading_ratio"] == 0.0
    assert profile["mixed_content_score"] == 0.0
    assert profile["avg_text_block_length"] == 0.0
    assert profile["table_avg_rows"] == 0.0


def test_analyze_blocks_word_count() -> None:
    blocks = [
        _b("TEXT", "one two three"),
        _b("HEADING", "## four five"),
    ]
    profile = analyze_document_blocks(blocks)
    assert profile["total_words"] == 5
