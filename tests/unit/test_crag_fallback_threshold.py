"""CRAG fallback minimum score discriminates noise.

The live ``crag_min_fallback_score`` row had drifted to ``0.01`` while
the canonical default in ``constants.DEFAULT_CRAG_MIN_FALLBACK_SCORE`` is
``0.3``. With the drift in place every RRF candidate (whose score falls
in the 0.01-0.05 range) passed the gate, so the CRAG fallback never
returned the OOS template — irrelevant chunks reached generate, the
faithfulness check refused them, and answer_relevancy dropped.

These tests pin the constant + seed value relationship so the drift
cannot reappear silently.
"""

from __future__ import annotations


def test_crag_fallback_default_above_rrf_window():
    """0.3 must be above the typical RRF score window (~0.01-0.05) so a
    bypass-mode rerank pipeline can never accidentally feed noise into
    generate via the CRAG fallback path.
    """
    from ragbot.shared.constants import DEFAULT_CRAG_MIN_FALLBACK_SCORE

    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE >= 0.3
    # Must also be ≤ 1.0 so an active-mode reranker (0..1 scale) can still
    # admit relevant chunks.
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE <= 1.0


def test_init_system_config_seed_matches_constant():
    """The seed file must store the canonical default verbatim."""
    from pathlib import Path

    from ragbot.shared.constants import DEFAULT_CRAG_MIN_FALLBACK_SCORE

    seed_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "init_system_config.py"
    )
    body = seed_path.read_text(encoding="utf-8")

    needle = (
        f'("crag_min_fallback_score", "{DEFAULT_CRAG_MIN_FALLBACK_SCORE}"'
    )
    assert needle in body, (
        "init_system_config seed value drifted from "
        "DEFAULT_CRAG_MIN_FALLBACK_SCORE; expected substring: "
        f"{needle!r}"
    )


def test_crag_fallback_filter_drops_below_threshold():
    """Behavioural sanity — a Python list-comprehension equivalent to the
    CRAG fallback filter must drop every chunk whose ``score`` is below
    the canonical threshold when the upstream is bypass-mode RRF.
    """
    from ragbot.shared.constants import DEFAULT_CRAG_MIN_FALLBACK_SCORE

    # Synthesised RRF-shaped candidates (typical bypass output).
    rrf_candidates = [
        {"id": "c1", "score": 0.018},
        {"id": "c2", "score": 0.024},
        {"id": "c3", "score": 0.031},
        {"id": "c4", "score": 0.045},
    ]
    min_score = DEFAULT_CRAG_MIN_FALLBACK_SCORE
    kept = [c for c in rrf_candidates if float(c["score"]) >= min_score]

    # All RRF candidates must be dropped at the canonical threshold —
    # this is the discriminator that fires the OOS template instead of
    # serving noise.
    assert kept == [], (
        "CRAG fallback let RRF-shaped noise through; threshold not "
        "discriminating as expected."
    )

    # Active-mode cross-encoder candidates above 0.3 must still pass.
    rerank_candidates = [
        {"id": "r1", "score": 0.42},
        {"id": "r2", "score": 0.18},  # below — should drop
        {"id": "r3", "score": 0.89},
    ]
    kept = [c for c in rerank_candidates if float(c["score"]) >= min_score]
    assert {c["id"] for c in kept} == {"r1", "r3"}
