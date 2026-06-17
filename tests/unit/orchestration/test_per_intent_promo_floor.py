"""Wave-2 Cluster C1 — per-intent promo/sale/voucher fallback floor.

Source-level invariants for the C1 fix. The HALLU Q7 Black Friday breach
(``reports/LOADTEST_POST_22TASKS_20260509_025928.json``) showed
top_score 0.181 confabulated as a Black Friday promo. Two defences:

1. C2 (separate branch) flipped ``DEFAULT_GROUNDING_CHECK_ENABLED``.
2. C1 (this branch) seeds per-intent CRAG floors:
   - ``factoid`` later re-calibrated 0.35 → 0.25 by multi-agent-r4
     (commit 4e8a83d, 2026-05-15) because LOAD_TEST_VERDICT Q18
     Điều 45 showed 0.35 over-rejected legal/regulatory single-article
     queries (dense retrieval scores 0.30..0.40). HALLU=0 sacred kept
     by Anti-fake-section sysprompt + grounding_check guardrail.
   - ``promo`` / ``sale`` / ``voucher`` = 0.40 (forward-compatible
     dormant; activates when the classifier or vocabulary router emits
     these labels).

Tests are pure source-level (no LangGraph boot, no DB).
"""
from __future__ import annotations

import inspect

from ragbot.shared.constants import (
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT,
)


# ---------------------------------------------------------------------------
# 1. factoid re-calibrated to 0.25 — broaden recall on legal factoid queries.
# ---------------------------------------------------------------------------
def test_factoid_floor_bumped_to_thirty_five() -> None:
    """factoid CRAG floor pinned at 0.25 (T1-Smartness re-calibration).

    Original C1 ship raised 0.30 → 0.35; LOAD_TEST_VERDICT Q18 Điều 45
    later showed 0.35 over-rejected legal/regulatory factoid queries.
    Lowered to 0.25 by multi-agent-r4 (commit 4e8a83d, 2026-05-15).
    HALLU=0 defence kept by Anti-fake-section sysprompt + downstream
    grounding gate.
    """
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"] == 0.25


def test_factoid_floor_strictly_above_global_default() -> None:
    """factoid floor is intentionally BELOW the global default after Q18
    re-calibration (0.25 vs 0.30). The per-intent gate is still active
    because the lookup order is "intent dict first, global default
    fallback" — a present-but-lower per-intent value just opens the
    admission band for factoid where legal/regulatory queries live.
    HALLU=0 stays sacred via the downstream Anti-fake sysprompt +
    grounding_check (not via this floor).
    """
    factoid = DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"]
    # Must remain a positive admission threshold (zero/-ve would be a
    # full no-op / nonsensical band).
    assert factoid > 0.0
    assert factoid < DEFAULT_CRAG_MIN_FALLBACK_SCORE, (
        "factoid floor pinned below the global default per Q18 calibration; "
        "if you see factoid >= global, the calibration was reverted — "
        "please re-validate against legal/regulatory load-test."
    )


# ---------------------------------------------------------------------------
# 2. promo / sale / voucher dormant gates seeded at 0.40.
# ---------------------------------------------------------------------------
def test_promo_sale_voucher_floors_seeded() -> None:
    """Forward-compat: when the intent classifier or vocabulary router
    starts emitting these labels, the strict 0.40 floor is already in
    place — no behavioural change until then."""
    for label in ("promo", "sale", "voucher"):
        assert label in DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT, (
            f"{label!r} not seeded in DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT"
        )
        assert (
            DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT[label] == 0.40
        ), f"{label!r} expected 0.40, got {DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT[label]}"


def test_promo_floor_strictly_above_factoid() -> None:
    """promo / sale / voucher floors must be stricter than factoid —
    pricing topics are the empirical HALLU CONFLATE hotspot."""
    factoid = DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"]
    for label in ("promo", "sale", "voucher"):
        assert (
            DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT[label] > factoid
        ), f"{label!r} floor must exceed factoid floor"


# ---------------------------------------------------------------------------
# 3. The orchestrator still consumes the constant (fallback path intact).
# ---------------------------------------------------------------------------
def test_query_graph_reads_per_intent_floor_from_pcfg() -> None:
    """The CRAG grader node must still resolve the per-intent floor via
    pipeline_config with the constant as final fallback.

    The grade node body was lifted out of ``build_graph`` into
    ``orchestration/nodes/grade.py`` (pure relocation); inspect that module's
    source instead. Whitespace-insensitive: the pin is the ``_pcfg`` call
    threading the key + the ``DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT``
    fallback, not a fixed indentation.
    """
    from ragbot.orchestration.nodes import grade as grade_module

    src = inspect.getsource(grade_module)
    compact = " ".join(src.split())
    expected = (
        '_pcfg( state, "crag_min_fallback_score_by_intent", '
        'DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT, )'
    )
    assert expected in compact, (
        "grade node must read crag_min_fallback_score_by_intent "
        "from pipeline_config with DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT "
        "fallback. Expected call shape not found."
    )


# ---------------------------------------------------------------------------
# 4. Q7 Black Friday regression simulation.
# ---------------------------------------------------------------------------
def test_q7_black_friday_top_score_below_factoid_floor() -> None:
    """Q7 Black Friday observed top_score = 0.181 (load test JSON).
    Post-C1, the factoid floor (0.35) MUST exclude this score so the
    chunk does not survive the CRAG fallback path."""
    q7_top_score = 0.181
    factoid_floor = DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["factoid"]
    assert q7_top_score < factoid_floor, (
        f"Q7 top_score ({q7_top_score}) must be below factoid floor "
        f"({factoid_floor}) so the chunk fails CRAG fallback"
    )


def test_promo_intent_floor_excludes_q7_top_score() -> None:
    """If the classifier ever routes Q7 to a 'promo' / 'sale' label,
    the 0.40 floor MUST also exclude the 0.181 top_score."""
    q7_top_score = 0.181
    for label in ("promo", "sale", "voucher"):
        floor = DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT[label]
        assert q7_top_score < floor, (
            f"{label} floor ({floor}) must exclude Q7 top_score ({q7_top_score})"
        )


# ---------------------------------------------------------------------------
# 5. Comparison + multi_hop deliberately permissive — preserve baseline.
# ---------------------------------------------------------------------------
def test_comparison_multi_hop_floor_unchanged() -> None:
    """multi-source intents need broader candidate admission; their
    floors stay at the prior baseline so we do not regress recall."""
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["comparison"] == 0.20
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT["multi_hop"] == 0.15


def test_baseline_intents_retained() -> None:
    """No baseline intent removed by the C1 ship."""
    for label in (
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
    ):
        assert label in DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT
