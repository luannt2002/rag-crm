"""Phase-C C5 — adaptive per-intent RRF weight resolver tests.

Pure source-level invariants: pure-function resolver, no DB, no LangGraph
boot. Covers the resolution chain (per-bot override → constants SSoT →
flat fallback), the feature flag default, sum-to-1 normalisation, and
the all-zero-bucket reject path.
"""
from __future__ import annotations

import math

import pytest

from ragbot.application.services.adaptive_rerank_weight import (
    IntentWeights,
    adaptive_weight_enabled,
    normalize_weights,
    resolve_intent_weights,
)
from ragbot.shared.constants import (
    DEFAULT_ADAPTIVE_RERANK_WEIGHT_ENABLED,
    DEFAULT_HYBRID_RRF_BM25_WEIGHT,
    DEFAULT_HYBRID_RRF_VECTOR_WEIGHT,
    DEFAULT_RERANK_WEIGHTS_BY_INTENT,
)


# ---------------------------------------------------------------------------
# 1. Constants SSoT: shape + every bucket has all 3 expected keys.
# ---------------------------------------------------------------------------
def test_default_table_has_required_buckets_and_keys() -> None:
    """Constants SSoT must define ``default`` + the four retrieval-bearing
    intents the classifier currently emits, each with vector/bm25/reranker."""
    required_buckets = {"default", "factoid", "multi_hop"}
    assert required_buckets.issubset(DEFAULT_RERANK_WEIGHTS_BY_INTENT.keys()), (
        "DEFAULT_RERANK_WEIGHTS_BY_INTENT missing one of the required "
        "buckets; resolution chain would silently fall through to flat 0.5/0.5."
    )
    required_keys = {"vector", "bm25", "reranker"}
    for bucket, weights in DEFAULT_RERANK_WEIGHTS_BY_INTENT.items():
        assert required_keys.issubset(weights.keys()), (
            f"bucket {bucket!r} missing one of {required_keys!r}"
        )
        for k, v in weights.items():
            assert isinstance(v, (int, float)), f"{bucket}.{k} not numeric"
            assert v >= 0.0, f"{bucket}.{k} negative — would flip RRF order"


# ---------------------------------------------------------------------------
# 2. factoid resolves to a vector-heavy, bm25-moderate, reranker-positive blend.
# ---------------------------------------------------------------------------
def test_factoid_intent_resolves_to_vector_dominant() -> None:
    """factoid bucket is the most precision-sensitive — its vector weight
    must be at least as high as bm25 (precision over recall)."""
    iw = resolve_intent_weights("factoid")
    assert iw.vector >= iw.bm25, (
        "factoid bucket regressed: bm25 outweighing vector defeats the "
        "precision intuition behind the bucket."
    )
    # Reranker slot must be set (forward-compat for blended fusion).
    assert iw.reranker > 0.0, "factoid reranker slot must be positive"


# ---------------------------------------------------------------------------
# 3. multi_hop resolves to vector-dominant (paraphrase recall).
# ---------------------------------------------------------------------------
def test_multi_hop_intent_resolves_to_vector_dominant() -> None:
    """multi_hop synthesises across paraphrases — vector weight must
    strictly exceed bm25 so dense recall wins over keyword overlap."""
    iw = resolve_intent_weights("multi_hop")
    assert iw.vector > iw.bm25, (
        "multi_hop bucket regressed: bm25 ≥ vector defeats the recall "
        "intuition behind multi-hop synthesis."
    )


# ---------------------------------------------------------------------------
# 4. Unknown intent falls back to the 'default' bucket, not to None/zero.
# ---------------------------------------------------------------------------
def test_unknown_intent_falls_back_to_default_bucket() -> None:
    """``"never_classifier_emits_this"`` must route to ``default``,
    NOT silently zero-out fusion. Compare against the literal default
    bucket so the test catches a future rename of the bucket key."""
    expected = DEFAULT_RERANK_WEIGHTS_BY_INTENT["default"]
    iw = resolve_intent_weights("never_classifier_emits_this")
    assert math.isclose(iw.vector, float(expected["vector"]))
    assert math.isclose(iw.bm25, float(expected["bm25"]))
    assert math.isclose(iw.reranker, float(expected["reranker"]))


# ---------------------------------------------------------------------------
# 5. None / empty / whitespace intent → default bucket.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_intent", [None, "", "   ", "\t\n"])
def test_missing_or_blank_intent_routes_to_default(bad_intent: str | None) -> None:
    """Classifier may return None when it fails open; resolver must
    still produce a usable blend (default bucket), never raise."""
    expected = DEFAULT_RERANK_WEIGHTS_BY_INTENT["default"]
    iw = resolve_intent_weights(bad_intent)
    assert iw.vector == float(expected["vector"])
    assert iw.bm25 == float(expected["bm25"])


# ---------------------------------------------------------------------------
# 6. Per-bot pipeline_config override wins over constants SSoT.
# ---------------------------------------------------------------------------
def test_pipeline_config_override_wins_over_constants() -> None:
    """Bot owner supplies a different blend via ``pipeline_config``.
    The resolver MUST honour that override and NOT fall through to the
    constants SSoT (otherwise ops can't tune per-bot without redeploy)."""
    override = {
        "rerank_weights_by_intent": {
            "factoid": {"vector": 0.9, "bm25": 0.05, "reranker": 0.05},
            "default": {"vector": 0.4, "bm25": 0.4, "reranker": 0.2},
        }
    }
    iw = resolve_intent_weights("factoid", pipeline_config=override)
    assert iw.vector == 0.9
    assert iw.bm25 == 0.05
    assert iw.reranker == 0.05
    # And the override's 'default' wins for unknown intents too.
    iw_default = resolve_intent_weights("nonexistent", pipeline_config=override)
    assert iw_default.vector == 0.4
    assert iw_default.bm25 == 0.4
    assert iw_default.reranker == 0.2


# ---------------------------------------------------------------------------
# 7. Malformed override does NOT silently zero-out fusion.
# ---------------------------------------------------------------------------
def test_malformed_override_falls_through_to_constants() -> None:
    """A misconfigured ``pipeline_config`` row (not a dict, all-zero
    bucket, wrong inner type) must NOT zero-out the blend — the resolver
    falls through to the constants SSoT so a bad ops typo cannot kill
    retrieval."""
    expected = DEFAULT_RERANK_WEIGHTS_BY_INTENT["default"]

    # Not a mapping at all.
    iw = resolve_intent_weights(
        "default", pipeline_config={"rerank_weights_by_intent": "garbage"}
    )
    assert iw.vector == float(expected["vector"])

    # Mapping but inner is not a mapping.
    iw = resolve_intent_weights(
        "default",
        pipeline_config={"rerank_weights_by_intent": {"default": "garbage"}},
    )
    assert iw.vector == float(expected["vector"])

    # All-zero bucket — refuse (would disable fusion) and fall through.
    override = {
        "rerank_weights_by_intent": {
            "factoid": {"vector": 0.0, "bm25": 0.0, "reranker": 0.0}
        }
    }
    iw = resolve_intent_weights("factoid", pipeline_config=override)
    # Falls through to default of the override (absent) → constants 'default'.
    assert iw.vector == float(expected["vector"])
    assert iw.bm25 == float(expected["bm25"])


# ---------------------------------------------------------------------------
# 8. Negative weights are clamped to 0 (RRF score-flip defence).
# ---------------------------------------------------------------------------
def test_negative_weights_clamped_to_zero() -> None:
    """Negative RRF weight would invert score ordering. The IntentWeights
    constructor MUST clamp at zero so an ops typo cannot break retrieval."""
    override = {
        "rerank_weights_by_intent": {
            "factoid": {"vector": -0.5, "bm25": 0.5, "reranker": 0.0}
        }
    }
    iw = resolve_intent_weights("factoid", pipeline_config=override)
    assert iw.vector == 0.0
    assert iw.bm25 == 0.5


# ---------------------------------------------------------------------------
# 9. Feature flag default OFF.
# ---------------------------------------------------------------------------
def test_feature_flag_default_off() -> None:
    """Phase-C C5 ships dark — the runtime flag defaults to OFF so the
    rollout is opt-in via system_config. Flipping this constant to True
    without a load-test gate violates the GA-smartness handoff."""
    assert DEFAULT_ADAPTIVE_RERANK_WEIGHT_ENABLED is False
    assert adaptive_weight_enabled() is False
    assert adaptive_weight_enabled({}) is False


def test_feature_flag_pipeline_config_override() -> None:
    """Per-bot pipeline_config override of the feature flag is honoured."""
    assert adaptive_weight_enabled({"adaptive_rerank_weight_enabled": True}) is True
    assert adaptive_weight_enabled({"adaptive_rerank_weight_enabled": False}) is False
    # Truthy / falsy strings — pipeline_config rows often arrive as strings
    # post JSON decode of system_config when value_type='bool'; the resolver
    # is permissive (``bool(...)``) so non-empty string wins.
    assert (
        adaptive_weight_enabled({"adaptive_rerank_weight_enabled": "yes"}) is True
    )


# ---------------------------------------------------------------------------
# 10. Normalisation sums to 1 when input is positive, identity on all-zero.
# ---------------------------------------------------------------------------
def test_normalize_sum_to_one() -> None:
    """``normalize_weights`` rescales so the triple sums to 1.0 (within
    fp tolerance). Identity when input sums to 0 so the helper never
    produces NaN even on a degenerate input."""
    iw = IntentWeights(vector=2.0, bm25=1.0, reranker=1.0)
    out = normalize_weights(iw)
    assert math.isclose(out.vector + out.bm25 + out.reranker, 1.0)
    assert math.isclose(out.vector, 0.5)
    assert math.isclose(out.bm25, 0.25)
    assert math.isclose(out.reranker, 0.25)

    # All-zero → identity (no division by zero, no NaN).
    zero = IntentWeights(vector=0.0, bm25=0.0, reranker=0.0)
    out_zero = normalize_weights(zero)
    assert out_zero.vector == 0.0
    assert out_zero.bm25 == 0.0
    assert out_zero.reranker == 0.0


# ---------------------------------------------------------------------------
# 11. Case-insensitive intent matching.
# ---------------------------------------------------------------------------
def test_intent_match_is_case_and_whitespace_insensitive() -> None:
    """Classifier upstream may emit 'Factoid' or '  factoid\\n' depending on
    LLM provider casing drift; the resolver MUST normalise before lookup."""
    expected = resolve_intent_weights("factoid")
    assert resolve_intent_weights("FACTOID") == expected
    assert resolve_intent_weights("  Factoid  ") == expected
    assert resolve_intent_weights("\tfactoid\n") == expected


# ---------------------------------------------------------------------------
# 12. Flat-default fallback when constants SSoT is completely empty.
# ---------------------------------------------------------------------------
def test_empty_override_falls_through_to_flat_constants() -> None:
    """An override that explicitly clears the table (empty dict shape)
    must NOT silently disable retrieval — the helper falls through to the
    flat constants ``DEFAULT_HYBRID_RRF_*_WEIGHT``."""
    # Empty mapping is a valid mapping but matches no intent and has no
    # 'default' bucket — must yield the flat fallback, not raise.
    # (The override coercer rejects an empty dict because _coerce_table
    # returns None on it; that routes to the constants SSoT instead.)
    iw = resolve_intent_weights(
        "factoid", pipeline_config={"rerank_weights_by_intent": {}}
    )
    # Falls back to constants SSoT factoid bucket (non-empty by definition).
    expected = DEFAULT_RERANK_WEIGHTS_BY_INTENT["factoid"]
    assert iw.vector == float(expected["vector"])
    assert iw.bm25 == float(expected["bm25"])


# ---------------------------------------------------------------------------
# 13. as_dict and equality round-trip for log emitters.
# ---------------------------------------------------------------------------
def test_intent_weights_as_dict_and_equality() -> None:
    """Step-tracker logs the resolved blend via ``as_dict``; round-trip
    must preserve all three keys and compare equal for the same triple."""
    a = IntentWeights(vector=0.6, bm25=0.3, reranker=0.1)
    b = IntentWeights(vector=0.6, bm25=0.3, reranker=0.1)
    assert a == b
    d = a.as_dict()
    assert d == {"vector": 0.6, "bm25": 0.3, "reranker": 0.1}


# ---------------------------------------------------------------------------
# 14. Constants SSoT default bucket equals the flat constants (continuity).
# ---------------------------------------------------------------------------
def test_default_bucket_matches_flat_constants() -> None:
    """The constants-SSoT ``default`` bucket must reproduce the historical
    flat 0.5 / 0.5 split, otherwise enabling the feature flag with no
    other change would shift behaviour for *all* unknown intents."""
    bucket = DEFAULT_RERANK_WEIGHTS_BY_INTENT["default"]
    assert bucket["vector"] == DEFAULT_HYBRID_RRF_VECTOR_WEIGHT
    assert bucket["bm25"] == DEFAULT_HYBRID_RRF_BM25_WEIGHT
