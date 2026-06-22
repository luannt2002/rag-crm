"""Phase-B Stream B1 — speculative parallel retrieve unit tests.

The orchestrator can race ``embed(raw_query) + hybrid_search`` against
the understand+rewrite chain. When the rewritten query is "close enough"
to the raw query (cosine_sim above threshold), the speculative chunks
become the retrieved set; otherwise they are discarded and the normal
retrieve path runs against the rewritten query.

These tests pin down the policy layer (pure cosine + decision helper) and
the orchestrator wiring invariants that must hold for the optimisation to
be safe (feature flag default OFF; cancellation on cache HIT; per-bot
threshold; graceful fallback on speculative failure).
"""

from __future__ import annotations

import math

import pytest

from ragbot.orchestration.nodes.speculative_retrieve import (
    cosine_similarity,
    decide_keep_speculative,
    intent_consumes_mq,
)
from ragbot.shared.constants import (
    DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT,
    DEFAULT_SPECULATIVE_RETRIEVE_ENABLED,
    DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S,
    DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD,
)
from ragbot.application.dto.llm_schemas import UnderstandOutput


# ---------------------------------------------------------------------------
# 1. Constant defaults — backward compatibility invariants.
# ---------------------------------------------------------------------------
def test_default_speculative_retrieve_enabled_is_false() -> None:
    """Default OFF — opt-in only. Flipping default to True would change
    the steady-state graph behaviour across every existing bot."""
    assert DEFAULT_SPECULATIVE_RETRIEVE_ENABLED is False


def test_default_speculative_similarity_threshold_in_valid_range() -> None:
    """Threshold must live in ``[0, 1]`` (cosine sim range) and be high
    enough that "raw and rewritten differ significantly" still triggers
    re-retrieve. 0.85 is the documented landing zone."""
    assert 0.0 < DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD <= 1.0
    assert DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD >= 0.75


def test_default_speculative_timeout_is_positive() -> None:
    """Timeout cap exists so a hung embed/search call cannot deadlock
    the wrapper. A 30s default is the documented MVP value."""
    assert DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S > 0
    assert DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S <= 60.0


# ---------------------------------------------------------------------------
# 2. cosine_similarity — pure numeric helper.
# ---------------------------------------------------------------------------
def test_cosine_similarity_identical_vectors_returns_one() -> None:
    """Two identical vectors must yield cosine_sim ≈ 1.0."""
    v = [0.5, 0.3, 0.7, 0.1, 0.4]
    sim = cosine_similarity(v, v)
    assert sim == pytest.approx(1.0, abs=1e-9)


def test_cosine_similarity_orthogonal_vectors_returns_zero() -> None:
    """Orthogonal vectors must yield cosine_sim ≈ 0.0."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    sim = cosine_similarity(v1, v2)
    assert sim == pytest.approx(0.0, abs=1e-9)


def test_cosine_similarity_anti_parallel_vectors_returns_negative_one() -> None:
    """Anti-parallel vectors must yield cosine_sim ≈ -1.0 (the policy
    layer treats negatives as "definitely not similar")."""
    v1 = [1.0, 2.0, 3.0]
    v2 = [-1.0, -2.0, -3.0]
    sim = cosine_similarity(v1, v2)
    assert sim == pytest.approx(-1.0, abs=1e-9)


def test_cosine_similarity_close_vectors_above_threshold() -> None:
    """Two near-identical embeddings (small perturbation) must clear the
    0.85 threshold — i.e. speculative keep is the correct decision."""
    v1 = [0.5, 0.3, 0.7, 0.1, 0.4]
    v2 = [0.51, 0.29, 0.71, 0.105, 0.398]
    sim = cosine_similarity(v1, v2)
    assert sim > DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD
    assert sim <= 1.0


def test_cosine_similarity_empty_input_returns_zero() -> None:
    """Empty vector ⇒ 0.0 (never raise). The pipeline calls this on hot
    path; a single bad embed must not break it."""
    assert cosine_similarity([], [1.0, 2.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], []) == 0.0
    assert cosine_similarity([], []) == 0.0


def test_cosine_similarity_length_mismatch_returns_zero() -> None:
    """Dim mismatch ⇒ 0.0 (defensive; never raise)."""
    sim = cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0])
    assert sim == 0.0


def test_cosine_similarity_zero_norm_returns_zero() -> None:
    """Zero-norm vectors are undefined for cosine; helper returns 0.0
    rather than raising ``ZeroDivisionError`` on hot path."""
    sim = cosine_similarity([0.0, 0.0, 0.0], [1.0, 2.0, 3.0])
    assert sim == 0.0


def test_cosine_similarity_finite_for_real_dim_vector() -> None:
    """8-dim embedding (common in tests) — verify finite + bounded."""
    v1 = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    v2 = [0.2, 0.1, 0.4, 0.3, 0.6, 0.5, 0.8, 0.7]
    sim = cosine_similarity(v1, v2)
    assert math.isfinite(sim)
    assert -1.0 <= sim <= 1.0


# ---------------------------------------------------------------------------
# 3. decide_keep_speculative — policy gate.
# ---------------------------------------------------------------------------
def test_decide_keep_speculative_above_threshold_returns_true() -> None:
    """When cosine_sim clears threshold, keep speculative chunks."""
    raw = [0.5, 0.3, 0.7, 0.1, 0.4]
    rewritten = [0.51, 0.29, 0.71, 0.105, 0.398]
    keep = decide_keep_speculative(raw, rewritten, threshold=0.85)
    assert keep is True


def test_decide_keep_speculative_below_threshold_returns_false() -> None:
    """When cosine_sim drops below threshold, refuse speculative — the
    rewritten query diverged enough that the raw chunks are stale."""
    raw = [1.0, 0.0, 0.0, 0.0, 0.0]
    rewritten = [0.0, 1.0, 0.0, 0.0, 0.0]
    keep = decide_keep_speculative(raw, rewritten, threshold=0.85)
    assert keep is False


def test_decide_keep_speculative_missing_raw_embed_returns_false() -> None:
    """Missing speculative raw embedding ⇒ refuse keep (fail-safe)."""
    rewritten = [0.5, 0.3, 0.7]
    keep = decide_keep_speculative(None, rewritten, threshold=0.85)
    assert keep is False


def test_decide_keep_speculative_missing_rewritten_returns_false() -> None:
    """Missing rewritten embedding ⇒ refuse keep — we have no proof the
    speculative chunks remain relevant to the rewritten intent."""
    raw = [0.5, 0.3, 0.7]
    keep = decide_keep_speculative(raw, None, threshold=0.85)
    assert keep is False


def test_decide_keep_speculative_zero_threshold_returns_false() -> None:
    """``threshold <= 0`` is interpreted as "speculative disabled" —
    refuse to keep even when vectors are identical."""
    v = [0.5, 0.3, 0.7]
    keep = decide_keep_speculative(v, v, threshold=0.0)
    assert keep is False


def test_decide_keep_speculative_exact_threshold_inclusive() -> None:
    """Equality with threshold counts as keep — sim == 1.0 with
    threshold == 1.0 means perfect overlap, definitely keep."""
    v = [0.5, 0.3, 0.7]
    keep = decide_keep_speculative(v, v, threshold=1.0)
    assert keep is True


def test_decide_keep_speculative_empty_embed_returns_false() -> None:
    """Empty embedding list (helper returned 0.0) ⇒ refuse keep."""
    raw: list[float] = []
    rewritten = [0.5, 0.3]
    keep = decide_keep_speculative(raw, rewritten, threshold=0.85)
    assert keep is False


# ---------------------------------------------------------------------------
# 3b. intent_consumes_mq — speculative MQ consume-gate (M16 regression).
#
# The speculative multi-query expansion stashes paraphrases under
# ``_mq_speculative_variants`` ONLY when the resolved intent benefits from
# multi-query fanout. The gate MUST decide using the canonical intent
# taxonomy (the classifier's ``UnderstandOutput.intent`` Literal); a label
# the classifier never emits is dead — its branch can never match and the
# already-paid-for variants are silently discarded.
# ---------------------------------------------------------------------------
def test_intent_consumes_mq_accepts_mq_enabled_canonical_intents() -> None:
    """Every intent flagged True in the per-intent MQ map must consume the
    speculative variants. ``aggregation`` + ``comparison`` were dropped by
    the old hardcoded set — this pins them back in."""
    mq_map = DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT
    for label, enabled in mq_map.items():
        if enabled:
            assert intent_consumes_mq(label, mq_map) is True, (
                f"MQ-enabled intent {label!r} must consume speculative variants"
            )


def test_intent_consumes_mq_rejects_mq_disabled_canonical_intents() -> None:
    """Lightweight intents (factoid/chitchat/greeting/…) skip MQ fanout —
    the gate must NOT keep variants for them."""
    mq_map = DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT
    for label, enabled in mq_map.items():
        if not enabled:
            assert intent_consumes_mq(label, mq_map) is False, (
                f"MQ-disabled intent {label!r} must not consume variants"
            )


def test_intent_consumes_mq_only_uses_canonical_classifier_labels() -> None:
    """M16 root-cause guard: the gate must operate over labels the
    classifier can actually emit. Phantom labels (``synthesis``,
    ``compound``, ``docs_only``) are NOT in ``UnderstandOutput.intent`` —
    they can never match a resolved intent, so the gate must treat them as
    non-consuming (no accidental keep on a label that never occurs)."""
    valid_labels = set(UnderstandOutput.model_fields["intent"].annotation.__args__)
    for phantom in ("synthesis", "compound", "docs_only"):
        assert phantom not in valid_labels, (
            f"{phantom!r} leaked into the classifier taxonomy"
        )
        assert (
            intent_consumes_mq(phantom, DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT)
            is False
        )


def test_intent_consumes_mq_aggregation_is_consumed() -> None:
    """Direct regression: ``aggregation`` (a real synthesis-bucket intent
    that pays for MQ variants) was silently discarded by the old set."""
    assert (
        intent_consumes_mq("aggregation", DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT)
        is True
    )


def test_intent_consumes_mq_honours_per_bot_override_map() -> None:
    """When a bot supplies its own ``multi_query_enabled_by_intent`` map the
    gate must follow it (config-driven, not a hardcoded constant)."""
    override = {"factoid": True, "multi_hop": False}
    assert intent_consumes_mq("factoid", override) is True
    assert intent_consumes_mq("multi_hop", override) is False


# ---------------------------------------------------------------------------
# 4. Source-level wiring guards — query_graph imports the helper.
# ---------------------------------------------------------------------------
def test_query_graph_imports_decide_keep_speculative() -> None:
    """The orchestration layer MUST consume the policy helper via import.
    Bypassing the helper (inlining cosine math) would re-introduce the
    untested decision branch this stream pins down."""
    from ragbot.orchestration import query_graph

    assert hasattr(query_graph, "_decide_keep_speculative")
    assert query_graph._decide_keep_speculative is decide_keep_speculative


def test_query_graph_imports_speculative_constants() -> None:
    """All three speculative knobs must be available at the orchestration
    layer — a missing import would mean the feature flag silently
    defaults to ``False`` even when the operator flipped the DB row."""
    from ragbot.orchestration import query_graph

    assert (
        query_graph.DEFAULT_SPECULATIVE_RETRIEVE_ENABLED
        is DEFAULT_SPECULATIVE_RETRIEVE_ENABLED
    )
    assert (
        query_graph.DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD
        == DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD
    )
    assert (
        query_graph.DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S
        == DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S
    )


def test_state_has_speculative_slots() -> None:
    """GraphState carries the three slots the orchestrator stashes —
    raw embed, chunks, hit-flag. Dropping any of these would break the
    contract between the parallel wrapper and ``retrieve``."""
    from ragbot.orchestration.state import GraphState

    annotations = GraphState.__annotations__
    assert "_speculative_raw_embed" in annotations
    assert "_speculative_chunks" in annotations
    assert "_speculative_hit" in annotations
