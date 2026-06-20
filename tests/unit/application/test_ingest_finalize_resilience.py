"""Pin tests — ingest finalize resilience (2026-06-20).

The finalize stage flipped a doc to ``state='failed'`` on ANY null-embedding
leaf. Because the chat readiness gate serves only ``state='active'`` and the
recovery sweep does NOT re-process ``'failed'``, a single TRANSIENT embed miss
(a provider 429 on one batch) turned a 1/500 doc PERMANENTLY dark — the
mid-session outage + the xe-1 churn this session both came from this.

Fix: ``_decide_ingest_state`` serves a doc that is MOSTLY embedded (leaf-embed
coverage >= floor); the null leaves keep BM25 retrievability. Only a genuinely
broken doc (no chunks, nothing embedded, or coverage below floor) fails.
"""

from __future__ import annotations

from ragbot.application.services.document_service.ingest_stages_final import (
    _decide_ingest_state,
)
from ragbot.shared.constants import DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE

_FLOOR = DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE  # 0.8


def _state(total: int, embedded: int, null_leaf: int, floor: float = _FLOOR) -> str:
    return _decide_ingest_state(total, embedded, null_leaf, min_leaf_coverage=floor)


def test_zero_chunks_fails() -> None:
    assert _state(0, 0, 0) == "failed"


def test_nothing_embedded_fails() -> None:
    # 10 leaves, none embedded → genuinely broken.
    assert _state(10, 0, 10) == "failed"


def test_all_leaves_embedded_active() -> None:
    assert _state(100, 100, 0) == "active"


def test_one_transient_miss_in_500_serves() -> None:
    """The xe-1 churn case: 1 null leaf out of ~500 (coverage 99.8%) must SERVE,
    not go dark."""
    assert _state(500, 499, 1) == "active"


def test_partial_at_floor_serves() -> None:
    # 26/32 leaves embedded = 0.8125 >= 0.8 → serve (the spa-2 partial case).
    assert _state(32, 26, 6) == "active"


def test_badly_broken_below_floor_fails() -> None:
    # 50/100 leaves embedded = 0.5 < 0.8 → genuinely degraded → re-ingest.
    assert _state(100, 50, 50) == "failed"


def test_floor_boundary_inclusive() -> None:
    # Exactly at the floor (8 embedded / 10 leaves = 0.8) → serve (>= is inclusive).
    assert _state(10, 8, 2, floor=0.8) == "active"
    # Just below (7/10 = 0.7) → fail.
    assert _state(10, 7, 3, floor=0.8) == "failed"


def test_parents_not_counted_against_coverage() -> None:
    """null_non_parent is leaves only — parents (NULL by design) are already
    excluded by the caller's SQL, so a doc with embedded leaves + NULL parents
    (null_non_parent=0) is active regardless of total>embedded."""
    # 100 rows, 60 embedded leaves, 40 parents (null_non_parent=0).
    assert _state(100, 60, 0) == "active"
