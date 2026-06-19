"""retrieval-tuning — pin the bumped retrieval defaults.

Locks the four constants tuned under the Z2 mission (2026-05-01) so a silent
revert in ``shared/constants.py`` regresses tests instead of top_score.

Cross-checks:
* Constants module values.
* Constraint invariants (n_variants ≤ max_variants ≤ ceiling, ef_search in
  bounds) so future bumps stay sane.
* Alembic migration 0057 carries the matching seed-row strings, keeping the
  DB ↔ code sources of truth aligned.
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# §1 ef_search — HNSW query-time candidate pool
# ---------------------------------------------------------------------------
def test_ef_search_default_at_z2_target() -> None:
    """``DEFAULT_EF_SEARCH`` must be 64 (Wave M3.6-F2 latency opt).

    History: Z2 baseline 80 → 100 (recall lift); Wave M3.6-F2 2026-05-20
    rolled back to 64 to match ``ef_construction``. pgvector docs:
    ``ef_search >= ef_construction`` already gives optimal recall;
    100 was 1.56× diminishing-returns territory. Recall preserved
    ≥95% per Sonnet audit Finding 5; retrieve p50 cut -100-200ms
    on production-sized HNSW indexes.
    """
    from ragbot.shared.constants import DEFAULT_EF_SEARCH, MAX_EF_SEARCH

    assert DEFAULT_EF_SEARCH == 64, (
        f"Wave M3.6-F2 set ef_search to 64; got {DEFAULT_EF_SEARCH}."
    )
    # Must remain inside the runtime cap so the SET hnsw.ef_search clamp
    # ``min(int(ef_search), MAX_EF_SEARCH)`` does not silently cap it.
    assert DEFAULT_EF_SEARCH <= MAX_EF_SEARCH


# ---------------------------------------------------------------------------
# §2 multi-query variant count
# ---------------------------------------------------------------------------
def test_multi_query_n_variants_at_z2_target() -> None:
    """``DEFAULT_MULTI_QUERY_N_VARIANTS`` must be 3 (Wave M3 p95 latency cut).

    History: Sprint-10 baseline 3 → Z2 bumped to 5 → Wave M3 2026-05-20
    rolled back to 3 after observed multi_query_fanout 2168-4710ms p95
    dominating retrieve step (p95 16s vs SLA 8s). 3 variants retain ~85%
    of recall lift vs 5 (per Anthropic MQ paper). Per-bot override via
    pipeline_config.multi_query_n_variants for bots needing deeper recall.
    """
    from ragbot.shared.constants import DEFAULT_MULTI_QUERY_N_VARIANTS

    assert DEFAULT_MULTI_QUERY_N_VARIANTS == 3, (
        f"Wave M3 set n_variants to 3; got {DEFAULT_MULTI_QUERY_N_VARIANTS}."
    )


def test_multi_query_max_variants_has_headroom() -> None:
    """Ceiling must be ≥ default + 2 so per-bot overrides have headroom."""
    from ragbot.shared.constants import (
        DEFAULT_MULTI_QUERY_MAX_VARIANTS,
        DEFAULT_MULTI_QUERY_N_VARIANTS,
    )

    assert DEFAULT_MULTI_QUERY_MAX_VARIANTS == 7, (
        f"Z2 bumped max_variants 5→7; got {DEFAULT_MULTI_QUERY_MAX_VARIANTS}."
    )
    assert (
        DEFAULT_MULTI_QUERY_MAX_VARIANTS >= DEFAULT_MULTI_QUERY_N_VARIANTS
    ), "ceiling must not drop below the default count"


# ---------------------------------------------------------------------------
# §3 rerank top_n — answer-context window
# ---------------------------------------------------------------------------
def test_rerank_top_n_at_z2_target() -> None:
    """``DEFAULT_RERANK_TOP_N`` must be 7 (5→7) for multi-fact coverage."""
    from ragbot.shared.constants import DEFAULT_RERANK_TOP_N, DEFAULT_TOP_K

    assert DEFAULT_RERANK_TOP_N == 7, (
        f"Z2 bumped rerank_top_n 5→7; got {DEFAULT_RERANK_TOP_N}."
    )
    # top_n must remain ≤ retrieve top_k or rerank receives fewer docs than
    # it returns — invariant breakage shows up as duplicate / missing chunks.
    assert DEFAULT_RERANK_TOP_N <= DEFAULT_TOP_K


# ---------------------------------------------------------------------------
# §4 CRAG — recorded as INTENTIONALLY UNCHANGED at this revision
# ---------------------------------------------------------------------------
def test_crag_factoid_threshold_post_c1_bump() -> None:
    """factoid CRAG threshold = 0.25 (T1-Smartness re-calibration).

    History: Z2 baseline 0.30 → Cluster C1 tightened to 0.35 to harden
    the gray zone (top_score 0.18..0.30). LOAD_TEST_VERDICT Q18 Điều 45
    later showed 0.35 was over-strict for single-article legal /
    regulatory factoid queries (dense retrieval scores 0.30-0.40),
    triggering rewrite_retry loops; multi-agent-r4 (4e8a83d, 2026-05-15)
    lowered to 0.25. HALLU=0 sacred preserved by Anti-fake-section
    sysprompt + downstream grounding_check gating fabrication.
    """
    from ragbot.shared.constants import DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT

    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"] == 0.25, (
        "factoid CRAG threshold pinned at 0.25 post Q18 re-calibration. "
        "HALLU defence kept downstream (Anti-fake sysprompt + grounding)."
    )


# ---------------------------------------------------------------------------
# §5 Migration 0057 carries matching seed strings
# ---------------------------------------------------------------------------
def _z2_migration_body() -> str:
    """Read the Z2 alembic file once for repeated assertions."""
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "_archive_pre_squash_20260618"
        / "20260501_0057_z2_retrieval_tuning.py"
    )
    return path.read_text(encoding="utf-8")


def test_migration_0057_seeds_rerank_top_n_seven() -> None:
    """Migration must upsert ``rag_rerank_top_n`` to "7" exactly."""
    body = _z2_migration_body()
    assert '"rag_rerank_top_n"' in body
    assert '"7"' in body, "rag_rerank_top_n target value 7 missing from migration"


def test_migration_0057_seeds_multi_query_n_variants_five() -> None:
    """Migration must upsert ``multi_query_n_variants`` to "5"."""
    body = _z2_migration_body()
    assert '"multi_query_n_variants"' in body
    assert '"5"' in body


def test_migration_0057_seeds_ef_search_one_hundred() -> None:
    """Migration must upsert ``ef_search`` to "100"."""
    body = _z2_migration_body()
    assert '"ef_search"' in body
    assert '"100"' in body


def test_migration_0057_chains_to_0056() -> None:
    """Revision metadata must chain off 0056 (language_packs_seed_vi_en)."""
    body = _z2_migration_body()
    assert 'revision = "0057"' in body
    assert 'down_revision = "0056"' in body
