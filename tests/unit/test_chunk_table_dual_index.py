"""Phase B — table_dual_index chunking.

Dual-index emits BOTH a whole-table group chunk (for aggregation / "list-all"
queries that must see every row at once) AND the existing per-row chunks (for
precise single-row lookup). This fixes the recall miss where an aggregation
query retrieves only top-k row chunks and misses the rest of the table.
"""
from __future__ import annotations

from ragbot.shared.chunking import _chunk_table_dual_index, smart_chunk

_CSV = (
    "Dịch vụ,Giá\n"
    "Chăm sóc da,350000\n"
    "Trẻ hóa,1200000\n"
    "Triệt lông,500000\n"
    "Massage,450000\n"
    "Tắm trắng,1500000\n"
)
_SERVICES = ["Chăm sóc da", "Trẻ hóa", "Triệt lông", "Massage", "Tắm trắng"]


class TestTableDualIndex:
    def test_emits_whole_table_group_chunk_with_all_rows(self):
        chunks = _chunk_table_dual_index(_CSV)
        # At least one chunk must contain EVERY service (the aggregation chunk).
        whole = [c for c in chunks if all(s in c for s in _SERVICES)]
        assert whole, f"no whole-table chunk covering all services: {chunks}"

    def test_keeps_per_row_chunks_for_lookup(self):
        chunks = _chunk_table_dual_index(_CSV)
        # Each service must appear in at least one SINGLE-row chunk (header + 1
        # service row only — i.e. a chunk that does NOT contain a second
        # service). This guarantees precise lookup survives.
        for svc in _SERVICES:
            single = [
                c for c in chunks
                if svc in c and sum(1 for s in _SERVICES if s in c) == 1
            ]
            assert single, f"no single-row lookup chunk for {svc!r}"

    def test_large_table_splits_into_multiple_group_chunks_covering_all(self):
        # Force a tiny group cap so the whole table can't fit in one group
        # chunk; the union of group chunks must still cover every row.
        chunks = _chunk_table_dual_index(_CSV, group_max_chars=50)
        group_chunks = [
            c for c in chunks if sum(1 for s in _SERVICES if s in c) >= 2
        ]
        assert group_chunks, "expected multi-row group chunks"
        covered = {s for s in _SERVICES if any(s in c for c in group_chunks)}
        assert covered == set(_SERVICES), f"group chunks miss rows: {covered}"

    def test_smart_chunk_dispatches_table_dual_index(self):
        # When the resolved table strategy is dual_index, smart_chunk routes
        # CSV docs through the dual-index path (whole-table chunk present).
        chunks = smart_chunk(_CSV, table_strategy="table_dual_index")
        assert any(all(s in c for s in _SERVICES) for c in chunks)

    def test_default_table_strategy_unchanged_row_as_chunk(self):
        # Behaviour-neutral: default table_strategy keeps pure row-as-chunk
        # (no whole-table chunk).
        chunks = smart_chunk(_CSV)
        assert not any(all(s in c for s in _SERVICES) for c in chunks)
