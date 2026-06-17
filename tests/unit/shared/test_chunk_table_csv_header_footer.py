"""Pin tests for ``_chunk_table_csv_with_context`` header + footer emission.

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 1.

Bug evidence (verified 2026-05-21 session): a mixed-content document
with 17 lines = 4-line intro paragraph + 12-row CSV table + 2-line
trailing notes. The pre-fix ``_chunk_table_csv`` emitted ONLY 12 chunks
(one per data row), dropping both the intro paragraph AND the trailing
notes. LLM retrieval for queries about top-level service description
or promotions could never surface those facts.

Fix: ``_chunk_table_csv_with_context`` emits 3 chunk types when the
feature flag is ON — header chunk (pre + first N rows), row chunks
(prior), footer chunk (last M rows + post). Default OFF preserves
byte-identical current behaviour.
"""

from __future__ import annotations

from ragbot.shared.chunking import (
    _chunk_table_csv,
    _chunk_table_csv_with_context,
    _detect_csv_regions,
)


# Synthetic mixed-content doc — pattern-level, domain-neutral. Numbers
# and placeholder names only, no brand or industry literal.
_MIXED_DOC = """Section title prose line
- Bullet 1 introducing the section
- Bullet 2 about timing characteristics
- Bullet 3 about pricing teaser
STT,Item,Unit price,Bundle price
1,Alpha,129000,899000
2,Beta,249000,1499000
3,Gamma,199000,1199000
4,Delta,349000,2399000
5,Epsilon,499000,2999000
12,Zeta,249000,1499000
Trailing note one about promotion
Trailing note two about warranty
"""

_PURE_CSV = """col_a,col_b,col_c
1,foo,100
2,bar,200
3,baz,300
"""


def test_flag_off_preserves_prior_behaviour() -> None:
    """Flag OFF → identical chunk count + content to prior _chunk_table_csv."""
    prior = _chunk_table_csv(_MIXED_DOC)
    new = _chunk_table_csv_with_context(_MIXED_DOC, header_footer_enabled=False)
    assert new == prior


def test_flag_on_mixed_doc_emits_header_chunk() -> None:
    """Header chunk MUST contain intro paragraph + csv header + sample rows."""
    chunks = _chunk_table_csv_with_context(
        _MIXED_DOC, header_footer_enabled=True,
    )
    # First chunk = synthetic header chunk (pre text comes first).
    assert "Bullet 1" in chunks[0]
    assert "timing" in chunks[0]
    assert "pricing" in chunks[0]
    assert "STT,Item" in chunks[0]  # csv header preserved
    assert "Alpha" in chunks[0]  # first sample data row included


def test_flag_on_mixed_doc_emits_footer_chunk() -> None:
    """Footer chunk MUST contain trailing notes."""
    chunks = _chunk_table_csv_with_context(
        _MIXED_DOC, header_footer_enabled=True,
    )
    last = chunks[-1]
    assert "Trailing note one" in last
    assert "Trailing note two" in last
    assert "STT,Item" in last  # csv header still anchors the footer


def test_flag_on_mixed_doc_keeps_all_row_chunks() -> None:
    """All data tuples remain retrievable in row chunks regardless of flag."""
    chunks = _chunk_table_csv_with_context(
        _MIXED_DOC, header_footer_enabled=True,
    )
    joined = "\n".join(chunks)
    # 1499000 appears in 2 data rows (Beta + Zeta) — both must be present.
    assert joined.count("1499000") >= 2
    assert "Zeta" in joined
    assert "Alpha" in joined


def test_flag_on_mixed_doc_chunk_count_includes_header_footer() -> None:
    """Mixed doc with non-trivial pre + post → row_count + 2 chunks."""
    chunks = _chunk_table_csv_with_context(
        _MIXED_DOC, header_footer_enabled=True,
    )
    # 6 data rows (Alpha, Beta, Gamma, Delta, Epsilon, Zeta) + 1 header + 1 footer.
    row_chunks = [
        c for c in chunks
        if c.count("\n") == 1
        and any(price in c for price in (",899000", ",1499000", ",1199000", ",2399000", ",2999000"))
    ]
    # Loose: at least 6 row chunks present
    assert len(row_chunks) >= 6
    # Total chunks > row count (header + footer added)
    assert len(chunks) >= len(row_chunks) + 2


def test_flag_on_pure_csv_no_synthetic_chunks() -> None:
    """Pure CSV (no pre/post non-trivial region) → only row chunks emitted."""
    chunks = _chunk_table_csv_with_context(
        _PURE_CSV, header_footer_enabled=True,
    )
    # 3 data rows, no pre/post → 3 chunks identical to prior
    assert len(chunks) == 3
    for c in chunks:
        assert c.startswith("col_a,col_b,col_c")


def test_empty_input_returns_empty() -> None:
    assert _chunk_table_csv_with_context("", header_footer_enabled=True) == []
    assert _chunk_table_csv_with_context("   ", header_footer_enabled=True) == []
    assert _chunk_table_csv_with_context("\n\n", header_footer_enabled=True) == []


def test_single_line_returns_as_is() -> None:
    chunks = _chunk_table_csv_with_context(
        "only one line", header_footer_enabled=True,
    )
    assert chunks == ["only one line"]


def test_no_real_table_degrades_to_single_chunk() -> None:
    """When <2 CSV-shape lines, emit one chunk so caller doesn't lose data."""
    prose = """Paragraph one with no commas.
Paragraph two also no commas.
Final line."""
    chunks = _chunk_table_csv_with_context(prose, header_footer_enabled=True)
    assert len(chunks) == 1
    assert "Paragraph one" in chunks[0]
    assert "Final line" in chunks[0]


def test_detect_csv_regions_mixed_doc() -> None:
    """Direct test of the region detector on the mirror doc."""
    lines = [ln for ln in _MIXED_DOC.split("\n") if ln.strip()]
    pre, header_idx, last_data_idx, post = _detect_csv_regions(lines)
    # Pre = 4 intro lines (Section title + 3 bullets)
    assert len(pre) == 4
    assert "Bullet 1" in pre[1]
    # Header = "STT,Item,..."
    assert lines[header_idx].startswith("STT,Item")
    # Last data row = "12,Zeta,..."
    assert "Zeta" in lines[last_data_idx]
    # Post = 2 trailing notes
    assert len(post) == 2
    assert "Trailing note one" in post[0]
    assert "Trailing note two" in post[1]


def test_detect_csv_regions_pure_csv() -> None:
    """No pre/post region for pure CSV."""
    lines = [ln for ln in _PURE_CSV.split("\n") if ln.strip()]
    pre, header_idx, last_data_idx, post = _detect_csv_regions(lines)
    assert pre == []
    assert header_idx == 0
    assert last_data_idx == 3  # 4 lines total (header + 3 rows), zero-indexed → 3
    assert post == []


def test_oversized_header_chunk_dropped_logged() -> None:
    """Header chunk that exceeds max_chunk_chars is dropped, row chunks kept."""
    # Build a doc where pre text is huge (>1500 chars) so header chunk overflows.
    huge_pre = "x" * 2000
    doc = f"{huge_pre}\ncol,a,b\n1,foo,100\n2,bar,200\n"
    chunks = _chunk_table_csv_with_context(
        doc, header_footer_enabled=True, max_chunk_chars=1500,
    )
    # Row chunks still produced (2 rows)
    row_count = sum(1 for c in chunks if c.startswith("col,a,b") and "\n" in c)
    assert row_count == 2
    # Header chunk dropped → all chunks fit under cap
    for c in chunks:
        assert len(c) <= 1500
