"""Pin test: promote_vn_hierarchical_headings normalizes Roman → Arabic.

Pre-2026-05-27: 'Chương III' in source doc → '# Chương III' heading + chunks
stored '[Chương III > ...]' path. Query 'chương 3' missed.

After fix: 'Chương III' → '# Chương 3' → paths store arabic canonical so
queries with either form match the chunks.
"""
from __future__ import annotations

from ragbot.shared.chunking import promote_vn_hierarchical_headings
from ragbot.shared.constants import DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES


def _legal_doc_sample() -> str:
    """Build a minimal Vietnamese legal doc with enough markers to trigger
    promotion (>= DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES)."""
    n = max(DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES, 4)
    lines = []
    # Chapters (mix Roman + Arabic to test both paths)
    lines.append("Chương I")
    lines.append("QUY ĐỊNH CHUNG")
    lines.append("Điều 1. Phạm vi")
    lines.append("Some article body here.")
    lines.append("Chương II")
    lines.append("Điều 2. Áp dụng")
    lines.append("Chương III")
    lines.append("ĐIỀU KHOẢN THI HÀNH")
    lines.append("Điều 55. Trách nhiệm")
    # Add Mục with Roman
    lines.append("Mục V")
    lines.append("Điều 60. Hết.")
    return "\n".join(lines)


def test_promote_converts_chuong_roman_to_arabic() -> None:
    out = promote_vn_hierarchical_headings(_legal_doc_sample())
    # Roman → Arabic in all heading lines
    assert "# Chương 1" in out
    assert "# Chương 2" in out
    assert "# Chương 3" in out
    assert "## Mục 5" in out
    # No Roman form survives in promoted headings
    assert "# Chương III" not in out
    assert "# Chương II" not in out
    assert "# Chương I\n" not in out
    assert "## Mục V" not in out


def test_promote_preserves_arabic_chuong() -> None:
    """Arabic input → arabic output (idempotent)."""
    doc_lines = []
    n = max(DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES, 4)
    for i in range(1, n + 1):
        doc_lines.append(f"Chương {i}")
        doc_lines.append(f"Điều {i}. Body")
    doc = "\n".join(doc_lines)
    out = promote_vn_hierarchical_headings(doc)
    for i in range(1, n + 1):
        assert f"# Chương {i}" in out


def test_promote_idempotent() -> None:
    """Apply twice = apply once (no double-rewrite)."""
    doc = _legal_doc_sample()
    once = promote_vn_hierarchical_headings(doc)
    twice = promote_vn_hierarchical_headings(once)
    assert once == twice


def test_promote_preserves_dieu_arabic() -> None:
    out = promote_vn_hierarchical_headings(_legal_doc_sample())
    # Điều always arabic — must not be mangled
    assert "### Điều 1." in out
    assert "### Điều 55." in out
    assert "### Điều 60." in out


def test_promote_non_legal_doc_untouched() -> None:
    """Doc below match threshold → no promotion, no normalization."""
    doc = "Hello world\nThis is a paragraph.\nAnother line."
    out = promote_vn_hierarchical_headings(doc)
    assert out == doc  # unchanged
