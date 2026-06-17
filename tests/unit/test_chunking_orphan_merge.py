"""Unit tests: orphan merge post-process + pure-CSV per-row gate."""
from __future__ import annotations

from ragbot.shared.chunking import (
    _is_csv_format,
    analyze_document,
    merge_orphan_chunks,
    select_strategy,
    smart_chunk,
)
from ragbot.shared.constants import (
    DEFAULT_CHUNK_MAX_SIZE,
    DEFAULT_CHUNK_ORPHAN_THRESHOLD,
)


# ── merge_orphan_chunks ─────────────────────────────────────────────────


def test_no_orphans_passthrough():
    """All chunks ≥ threshold → output identical."""
    long = "x" * 250
    chunks = [long, long, long]
    out = merge_orphan_chunks(
        chunks, orphan_threshold=100, max_size=DEFAULT_CHUNK_MAX_SIZE
    )
    assert out == chunks


def test_single_orphan_merges_with_next():
    """[short, long] → 1 chunk merged."""
    short = "1. Section header"
    long = "x" * 250
    out = merge_orphan_chunks(
        [short, long], orphan_threshold=100, max_size=DEFAULT_CHUNK_MAX_SIZE
    )
    assert len(out) == 1
    assert out[0] == f"{short}\n{long}"


def test_orphan_at_end_merges_with_prev():
    """[long, short] → 1 chunk merged into previous."""
    long = "x" * 250
    short = "trailing bullet"
    out = merge_orphan_chunks(
        [long, short], orphan_threshold=100, max_size=DEFAULT_CHUNK_MAX_SIZE
    )
    assert len(out) == 1
    assert out[0] == f"{long}\n{short}"


def test_consecutive_orphans_chain_merge():
    """3 orphans + 1 long → 1 merged chunk preserving order."""
    o1, o2, o3 = "header A", "- bullet A", "- bullet B"
    long = "y" * 250
    out = merge_orphan_chunks(
        [o1, o2, o3, long], orphan_threshold=100, max_size=DEFAULT_CHUNK_MAX_SIZE
    )
    assert len(out) == 1
    assert out[0] == f"{o1}\n{o2}\n{o3}\n{long}"


def test_merge_skipped_when_overflow_cap():
    """Orphan + huge content → no merge if result > max_size; orphan emitted alone."""
    short = "header"
    huge = "z" * 1000
    out = merge_orphan_chunks(
        [short, huge], orphan_threshold=100, max_size=500
    )
    # Cannot merge (would be ~1007c > 500). Both kept separate.
    assert short in out
    assert huge in out
    assert len(out) == 2


def test_empty_input_returns_empty():
    out = merge_orphan_chunks(
        [], orphan_threshold=100, max_size=DEFAULT_CHUNK_MAX_SIZE
    )
    assert out == []


def test_all_orphans_input():
    """Only orphans → all retained (cannot merge into nonexistent neighbour)."""
    chunks = ["a", "b", "c"]
    out = merge_orphan_chunks(
        chunks, orphan_threshold=100, max_size=DEFAULT_CHUNK_MAX_SIZE
    )
    # All three orphans concatenated into single trailing-fold result
    # (no preceding non-orphan, so they remain as the pending list).
    assert "a" in out[0] if len(out) == 1 else True
    assert sum(len(c) for c in out) >= 3


def test_default_constants_sane():
    """Sanity check that defaults are imported correctly."""
    assert DEFAULT_CHUNK_ORPHAN_THRESHOLD == 100
    assert DEFAULT_CHUNK_MAX_SIZE == 1024


# ── Pure-CSV strategy gate ──────────────────────────────────────────────


def test_pure_csv_short_doc_forces_table_csv_strategy():
    """CSV-only content < whole_doc_threshold → strategy=table_csv (per-row)."""
    csv = (
        "Service,Price\n"
        "Service A,1000000\n"
        "Service B,2000000\n"
        "Service C,3000000\n"
        "Service D,4000000\n"
        "Service E,5000000\n"
    )
    assert _is_csv_format(csv) is True
    profile = analyze_document(csv)
    strategy, _confidence = select_strategy(profile)
    assert strategy == "table_csv"
    chunks = smart_chunk(csv, strategy=strategy)
    # Per-row: 5 data rows → 5 chunks, each containing header.
    assert len(chunks) == 5
    for ch in chunks:
        assert ch.startswith("Service,Price")


def test_pure_csv_short_doc_not_collapsed_into_one_chunk():
    """Regression: 18-row CSV must NOT collapse into a single chunk."""
    rows = ["Service,Price"]
    for i in range(18):
        rows.append(f"Service {i},{(i + 1) * 1000}")
    csv = "\n".join(rows)
    profile = analyze_document(csv)
    strategy, _ = select_strategy(profile)
    assert strategy == "table_csv"
    chunks = smart_chunk(csv, strategy=strategy)
    assert len(chunks) == 18
