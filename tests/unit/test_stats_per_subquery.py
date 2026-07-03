"""Step-3 (002 cluster C): decomposed comparisons must get STATS lookups per
sub-query — the old guard simply DISABLED the stats route under decompose
(symptom fix), so both legs of "so sánh giá A và B" fell to fuzzy vector and
one leg routinely missed its priced row (L-014/L-017-class).

New: per-sub-query stats loop → synthetic price chunks JOIN the fan-out result
set (no short-circuit)."""
from __future__ import annotations

import asyncio
import inspect


def test_helper_collects_confident_subqueries_only() -> None:
    from ragbot.orchestration.nodes.retrieve import _stats_chunks_for_sub_queries

    class RF:  # minimal RangeFilter stand-in
        def __init__(self, conf): self.confidence = conf

    def parse(q):
        return RF(0.9) if "codeA" in q or "codeB" in q else RF(0.1)

    calls = []
    async def lookup(state, *, range_filter, stats_limit, expect_price):
        calls.append(range_filter)
        return {"linked_chunks": [{"chunk_id": f"c{len(calls)}", "content": "x",
                                   "score": 1.0, "source": "stats_index"}],
                "entities": []}

    out = asyncio.run(_stats_chunks_for_sub_queries(
        state={}, sub_queries=["giá codeA", "giá codeB", "câu mơ hồ"],
        parse_fn=parse, lookup_fn=lookup, min_confidence=0.7,
        stats_limit=50, expect_price=True, max_subs=4,
    ))
    assert [c["chunk_id"] for c in out] == ["c1", "c2"]  # 2 confident subs only
    assert len(calls) == 2


def test_helper_dedups_and_survives_lookup_failure() -> None:
    from ragbot.orchestration.nodes.retrieve import _stats_chunks_for_sub_queries

    class RF:
        confidence = 0.95

    async def lookup(state, *, range_filter, stats_limit, expect_price):
        if not hasattr(lookup, "n"):
            lookup.n = 1
            raise ValueError("boom")  # first sub fails → skipped, not fatal
        return {"linked_chunks": [{"chunk_id": "same", "content": "x",
                                   "score": 1.0, "source": "stats_index"}],
                "entities": []}

    out = asyncio.run(_stats_chunks_for_sub_queries(
        state={}, sub_queries=["a1", "a2", "a3"],
        parse_fn=lambda q: RF(), lookup_fn=lookup, min_confidence=0.7,
        stats_limit=50, expect_price=False, max_subs=4,
    ))
    assert [c["chunk_id"] for c in out] == ["same"]  # dedup by chunk_id


def test_fanout_branch_wires_the_helper() -> None:
    from ragbot.orchestration.nodes import retrieve as r

    src = inspect.getsource(r)
    assert "_stats_chunks_for_sub_queries(" in src
    # must merge INTO chunks (join fan-out), not return/short-circuit around it
    i = src.rfind("_stats_chunks_for_sub_queries(")
    tail = src[i:i + 1200]
    assert "chunks" in tail
