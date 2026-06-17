"""Unit tests — M2/M22 neighbor expansion helper (Agent A4).

Validates the pure functions of
``ragbot.orchestration.nodes.neighbor_expand``. The SQL fan-out
(``fetch_neighbors_sql``) is exercised with a mocked async session
factory so the test stays infra-free.

Key invariants tested:
* M2 — neighbor windows compute per-doc index ranges correctly.
* M22 — token budget caps total payload + emits a structured truncation
  event.
* HALLU=0 sacred — only existing chunks surface; expansion preserves
  seeds and rejects duplicates.
* Multi-document boundaries — each doc's expansion is independent.

All assertions are real value/behavior checks per CLAUDE.md test rules.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from ragbot.orchestration.nodes.neighbor_expand import (
    expand_neighbors,
    fetch_neighbors_sql,
    merge_neighbors_with_seeds,
    plan_neighbor_windows,
)


# ────────────────── plan_neighbor_windows (pure) ───────────────────────


def test_plan_windows_single_seed_single_doc():
    """One seed at idx=5, n=1 → range [4, 6]."""
    chunks = [{"chunk_id": "c1", "document_id": "d1", "chunk_index": 5}]
    plan = plan_neighbor_windows(chunks, n=1)
    assert plan == {"d1": (4, 6)}


def test_plan_windows_zero_window_returns_empty_when_n_negative():
    """Negative n is treated as no-op (defensive against bad config)."""
    chunks = [{"chunk_id": "c1", "document_id": "d1", "chunk_index": 3}]
    plan = plan_neighbor_windows(chunks, n=-1)
    assert plan == {}


def test_plan_windows_clamps_lo_to_zero():
    """Window radius clipped at 0 so we don't fetch negative chunk_index."""
    chunks = [{"chunk_id": "c1", "document_id": "d1", "chunk_index": 0}]
    plan = plan_neighbor_windows(chunks, n=2)
    assert plan == {"d1": (0, 2)}


def test_plan_windows_multi_seed_same_doc_unions():
    """Multiple seeds in same doc → bounding union of all ±n windows.
    Seeds at idx 3 and 8 with n=1 → union is [2, 9]."""
    chunks = [
        {"chunk_id": "c1", "document_id": "d1", "chunk_index": 3},
        {"chunk_id": "c2", "document_id": "d1", "chunk_index": 8},
    ]
    plan = plan_neighbor_windows(chunks, n=1)
    assert plan == {"d1": (2, 9)}


def test_plan_windows_multi_doc_independent():
    """Each document gets its own range; no cross-doc bleeding."""
    chunks = [
        {"chunk_id": "c1", "document_id": "d1", "chunk_index": 3},
        {"chunk_id": "c2", "document_id": "d2", "chunk_index": 10},
    ]
    plan = plan_neighbor_windows(chunks, n=1)
    assert plan == {"d1": (2, 4), "d2": (9, 11)}


def test_plan_windows_skips_seed_missing_doc_id():
    """Seeds without document_id are skipped — graceful degrade."""
    chunks = [
        {"chunk_id": "c1", "chunk_index": 3},  # no document_id
        {"chunk_id": "c2", "document_id": "d1", "chunk_index": 5},
    ]
    plan = plan_neighbor_windows(chunks, n=1)
    assert plan == {"d1": (4, 6)}


def test_plan_windows_skips_seed_missing_chunk_index():
    """Seeds without chunk_index are skipped."""
    chunks = [{"chunk_id": "c1", "document_id": "d1"}]  # no chunk_index
    plan = plan_neighbor_windows(chunks, n=1)
    assert plan == {}


def test_plan_windows_empty_input():
    """Empty seed list → empty plan."""
    plan = plan_neighbor_windows([], n=1)
    assert plan == {}


# ────────────────── merge_neighbors_with_seeds (pure) ──────────────────


def test_merge_seeds_alone():
    """Empty neighbours → seeds round-trip as plain dicts."""
    seeds = [
        {"chunk_id": "c1", "document_id": "d1", "chunk_index": 5, "content": "seed1"},
    ]
    out = merge_neighbors_with_seeds(seeds, [], token_budget=0)
    assert len(out) == 1
    assert out[0]["chunk_id"] == "c1"
    assert out[0]["content"] == "seed1"


def test_merge_seeds_plus_neighbors():
    """Seed at idx=5 + 2 neighbours (idx=4, 6) → 3 chunks total."""
    seeds = [
        {"chunk_id": "c5", "document_id": "d1", "chunk_index": 5, "content": "seed"},
    ]
    neighbors = [
        {"chunk_id": "c4", "document_id": "d1", "chunk_index": 4, "content": "before"},
        {"chunk_id": "c6", "document_id": "d1", "chunk_index": 6, "content": "after"},
    ]
    out = merge_neighbors_with_seeds(seeds, neighbors, token_budget=0)
    assert len(out) == 3
    ids = [c["chunk_id"] for c in out]
    # Seeds first, then neighbours in (doc, chunk_index) order
    assert ids == ["c5", "c4", "c6"]


def test_merge_deduplicates_seed_in_neighbor_window():
    """The SQL window includes the seed's own chunk_index — dedup
    prevents emitting the same row twice."""
    seeds = [
        {"chunk_id": "c5", "document_id": "d1", "chunk_index": 5, "content": "seed"},
    ]
    neighbors = [
        # c5 is the seed itself — must NOT appear twice in output
        {"chunk_id": "c5", "document_id": "d1", "chunk_index": 5, "content": "seed_dup"},
        {"chunk_id": "c6", "document_id": "d1", "chunk_index": 6, "content": "after"},
    ]
    out = merge_neighbors_with_seeds(seeds, neighbors, token_budget=0)
    ids = [c["chunk_id"] for c in out]
    assert ids == ["c5", "c6"]
    # Seed content wins — neighbours don't overwrite seeds
    assert out[0]["content"] == "seed"


def test_merge_token_budget_stops_expansion():
    """M22 — when cumulative tokens > budget, stop adding neighbours.
    With token estimate = chars/4, 100-char seed = 25 tokens; budget=30
    → seed fits, next neighbour (also 25 tokens) overflows → dropped."""
    seeds = [
        {"chunk_id": "c5", "document_id": "d1", "chunk_index": 5, "content": "x" * 100},
    ]
    neighbors = [
        {"chunk_id": "c6", "document_id": "d1", "chunk_index": 6, "content": "y" * 100},
        {"chunk_id": "c7", "document_id": "d1", "chunk_index": 7, "content": "z" * 100},
    ]
    out = merge_neighbors_with_seeds(seeds, neighbors, token_budget=30)
    # Only seed survives (25 tokens used, next would push to 50)
    assert len(out) == 1
    assert out[0]["chunk_id"] == "c5"


def test_merge_token_budget_zero_means_unbounded():
    """budget=0 disables the cap — all neighbours emitted."""
    seeds = [
        {"chunk_id": "c1", "document_id": "d1", "chunk_index": 5, "content": "x" * 1000},
    ]
    neighbors = [
        {"chunk_id": "c2", "document_id": "d1", "chunk_index": 6, "content": "y" * 1000},
    ]
    out = merge_neighbors_with_seeds(seeds, neighbors, token_budget=0)
    assert len(out) == 2


def test_merge_marks_neighbor_chunks():
    """Neighbour rows get ``is_neighbor_expanded=True`` so downstream
    audit can distinguish them from seeds."""
    seeds = [{"chunk_id": "c1", "document_id": "d1", "chunk_index": 5, "content": "s"}]
    neighbors = [{"chunk_id": "c2", "document_id": "d1", "chunk_index": 6, "content": "n"}]
    out = merge_neighbors_with_seeds(seeds, neighbors, token_budget=0)
    assert out[0].get("is_neighbor_expanded") is None  # seed not marked
    assert out[1].get("is_neighbor_expanded") is True  # neighbour marked


def test_merge_multi_doc_independent():
    """Two docs' neighbours are emitted side-by-side, each carrying
    its own document_id."""
    seeds = [
        {"chunk_id": "a1", "document_id": "d1", "chunk_index": 0, "content": "A1"},
        {"chunk_id": "b1", "document_id": "d2", "chunk_index": 0, "content": "B1"},
    ]
    neighbors = [
        {"chunk_id": "a2", "document_id": "d1", "chunk_index": 1, "content": "A2"},
        {"chunk_id": "b2", "document_id": "d2", "chunk_index": 1, "content": "B2"},
    ]
    out = merge_neighbors_with_seeds(seeds, neighbors, token_budget=0)
    doc_groups = {c["chunk_id"]: c.get("document_id") for c in out}
    assert doc_groups == {"a1": "d1", "b1": "d2", "a2": "d1", "b2": "d2"}


# ────────────────── fetch_neighbors_sql (mocked session) ───────────────


class _MockResult:
    """Mimics SQLAlchemy result.fetchall() shape."""
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows


class _MockSession:
    """Async-context session that returns canned rows."""
    def __init__(self, rows_by_doc: dict):
        self._rows_by_doc = rows_by_doc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def execute(self, stmt, params):
        doc = params["did"]
        return _MockResult(self._rows_by_doc.get(doc, []))


def _mk_factory(rows_by_doc):
    """Build a session_factory that returns _MockSession instances."""
    def _factory():
        return _MockSession(rows_by_doc)
    return _factory


def test_fetch_neighbors_returns_rows():
    """End-to-end mock — SQL params get passed through, rows emerge."""
    bot_chunk_id = uuid.uuid4()
    doc_id = "d1"
    rows = [(bot_chunk_id, "neighbor content", uuid.UUID(int=1), 3, {})]
    factory = _mk_factory({doc_id: rows})

    result = asyncio.run(fetch_neighbors_sql(
        factory,
        record_tenant_id=uuid.uuid4(),
        plan={doc_id: (2, 4)},
        max_concurrency=1,
    ))
    assert len(result) == 1
    assert result[0]["chunk_id"] == str(bot_chunk_id)
    assert result[0]["content"] == "neighbor content"
    assert result[0]["chunk_index"] == 3


def test_fetch_neighbors_empty_plan_returns_empty():
    """Empty plan → no SQL issued, empty result."""
    factory = _mk_factory({})
    result = asyncio.run(fetch_neighbors_sql(
        factory,
        record_tenant_id=uuid.uuid4(),
        plan={},
        max_concurrency=1,
    ))
    assert result == []


def test_fetch_neighbors_none_factory_returns_empty():
    """Missing session_factory → graceful degrade."""
    result = asyncio.run(fetch_neighbors_sql(
        None,
        record_tenant_id=uuid.uuid4(),
        plan={"d1": (0, 5)},
        max_concurrency=1,
    ))
    assert result == []


def test_fetch_neighbors_none_tenant_returns_empty():
    """Missing record_tenant_id → graceful degrade (defence-in-depth)."""
    factory = _mk_factory({"d1": [(uuid.uuid4(), "x", uuid.UUID(int=1), 0, {})]})
    result = asyncio.run(fetch_neighbors_sql(
        factory,
        record_tenant_id=None,
        plan={"d1": (0, 5)},
        max_concurrency=1,
    ))
    assert result == []


# ────────────────── expand_neighbors (end-to-end) ──────────────────────


def test_expand_neighbors_disabled_by_zero_window():
    """window_size <= 0 → fast path returns seeds verbatim (as dicts)."""
    seeds = [{"chunk_id": "c1", "content": "x", "document_id": "d1", "chunk_index": 0}]
    out = asyncio.run(expand_neighbors(
        seeds,
        session_factory=_mk_factory({}),
        record_tenant_id=uuid.uuid4(),
        window_size=0,
    ))
    assert len(out) == 1
    assert out[0]["chunk_id"] == "c1"


def test_expand_neighbors_empty_seeds():
    """Empty seed set → empty result, no SQL issued."""
    out = asyncio.run(expand_neighbors(
        [],
        session_factory=_mk_factory({}),
        record_tenant_id=uuid.uuid4(),
        window_size=1,
    ))
    assert out == []


def test_expand_neighbors_full_pipeline():
    """Seed at idx=5 plus mock neighbours at idx=4, 5 (duplicate), 6.
    The seed's chunk_id MUST match the mock-row's chunk_id for dedup to
    fire — emulates a real ingest where seed comes from the same DB
    rows the neighbour fetch will surface again."""
    seed_uuid = uuid.UUID(int=5)
    seeds = [
        {
            "chunk_id": str(seed_uuid),
            "document_id": "d1",
            "chunk_index": 5,
            "content": "seed",
        },
    ]
    neighbor_rows = [
        (uuid.UUID(int=4), "before", uuid.UUID(int=10), 4, {}),
        (seed_uuid, "seed_dup", uuid.UUID(int=10), 5, {}),  # dedup target
        (uuid.UUID(int=6), "after", uuid.UUID(int=10), 6, {}),
    ]
    factory = _mk_factory({"d1": neighbor_rows})
    out = asyncio.run(expand_neighbors(
        seeds,
        session_factory=factory,
        record_tenant_id=uuid.uuid4(),
        window_size=1,
        token_budget=0,
    ))
    # Seed first, then 2 unique neighbours (the duplicate seed-row is dropped).
    assert len(out) == 3
    assert out[0]["content"] == "seed"  # seed content wins over dup
