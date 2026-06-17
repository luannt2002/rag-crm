"""Pin tests for 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 3 per-intent caps.

The rerank node + prompt_build context-cap reader must honour
``rerank_top_n_by_intent`` and ``generate_context_chars_cap_by_intent``
when the current intent is in the dict. Unknown intent / non-dict
config / missing key falls back to the global default.

We test the resolver logic in isolation (the same expression used
inline at the call sites) rather than spinning the LangGraph node,
because the rerank node has 200+ lines of orthogonal side-effects.
Same pattern as ``test_crag_skip_threshold.py`` for the existing
``DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT`` resolver.
"""

from __future__ import annotations

import pytest

from ragbot.shared.constants import (
    DEFAULT_GENERATE_CONTEXT_CHARS_CAP,
    DEFAULT_RERANK_TOP_N,
)


def _resolve_top_n(
    intent: str,
    by_intent_cfg: object,
    global_fallback: int,
) -> tuple[int, bool]:
    """Mirror of the rerank node resolver (kept in lock-step with code).

    Returns ``(top_n, intent_override_used)``.
    """
    if isinstance(by_intent_cfg, dict) and intent in by_intent_cfg:
        try:
            return (int(by_intent_cfg[intent]), True)
        except (TypeError, ValueError):
            return (global_fallback, False)
    return (global_fallback, False)


def _resolve_ctx_cap(
    intent: str,
    cap_by_intent: object,
    global_fallback: int,
) -> int:
    """Mirror of the prompt_build context-cap resolver."""
    if isinstance(cap_by_intent, dict) and intent in cap_by_intent:
        try:
            return int(cap_by_intent[intent])
        except (TypeError, ValueError):
            return global_fallback
    return global_fallback


# -- rerank_top_n_by_intent --------------------------------------------------


def test_aggregation_intent_boosts_rerank_top_n() -> None:
    by_intent = {"factoid": 7, "aggregation": 20}
    top_n, override = _resolve_top_n("aggregation", by_intent, 7)
    assert top_n == 20
    assert override is True


def test_factoid_intent_uses_factoid_cap() -> None:
    by_intent = {"factoid": 7, "aggregation": 20}
    top_n, override = _resolve_top_n("factoid", by_intent, 7)
    assert top_n == 7
    assert override is True


def test_unknown_intent_falls_back_to_global_default() -> None:
    by_intent = {"factoid": 7, "aggregation": 20}
    top_n, override = _resolve_top_n("totally_unknown", by_intent, 7)
    assert top_n == 7
    assert override is False


def test_empty_intent_falls_back_to_global_default() -> None:
    by_intent = {"factoid": 7, "aggregation": 20}
    top_n, override = _resolve_top_n("", by_intent, 7)
    assert top_n == 7
    assert override is False


def test_non_dict_config_falls_back_to_global_default() -> None:
    # Operator set the row to a string by mistake → must not crash.
    top_n, override = _resolve_top_n("aggregation", "not-a-dict", 7)
    assert top_n == 7
    assert override is False


def test_none_config_falls_back_to_global_default() -> None:
    top_n, override = _resolve_top_n("aggregation", None, 7)
    assert top_n == 7
    assert override is False


def test_malformed_value_falls_back_silently() -> None:
    # Operator typo: stringly-typed value that int() cannot parse.
    by_intent = {"aggregation": "twenty"}
    top_n, override = _resolve_top_n("aggregation", by_intent, 7)
    assert top_n == 7
    assert override is False


def test_default_constant_value_for_aggregation_intent() -> None:
    """The module-level default dict must boost aggregation above the
    global ``DEFAULT_RERANK_TOP_N``."""
    from ragbot.shared.constants import DEFAULT_RERANK_TOP_N_BY_INTENT

    assert (
        DEFAULT_RERANK_TOP_N_BY_INTENT["aggregation"] > DEFAULT_RERANK_TOP_N
    ), "aggregation must get a wider rerank funnel than the global default"
    assert DEFAULT_RERANK_TOP_N_BY_INTENT["factoid"] == DEFAULT_RERANK_TOP_N


# -- generate_context_chars_cap_by_intent ------------------------------------


def test_aggregation_intent_boosts_context_cap() -> None:
    by_intent = {"factoid": 2900, "aggregation": 5500}
    cap = _resolve_ctx_cap("aggregation", by_intent, 2900)
    assert cap == 5500


def test_factoid_intent_uses_factoid_context_cap() -> None:
    by_intent = {"factoid": 2900, "aggregation": 5500}
    cap = _resolve_ctx_cap("factoid", by_intent, 2900)
    assert cap == 2900


def test_unknown_intent_uses_global_context_cap_fallback() -> None:
    by_intent = {"factoid": 2900, "aggregation": 5500}
    assert _resolve_ctx_cap("unknown_intent", by_intent, 2900) == 2900


def test_default_constant_value_for_aggregation_context_cap() -> None:
    from ragbot.shared.constants import DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT

    assert (
        DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT["aggregation"]
        > DEFAULT_GENERATE_CONTEXT_CHARS_CAP
    )
    assert (
        DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT["factoid"]
        == DEFAULT_GENERATE_CONTEXT_CHARS_CAP
    )


# -- coverage of every named intent in DEFAULT_*_BY_INTENT dicts -------------


_CANONICAL_INTENTS = (
    "factoid",
    "comparison",
    "multi_hop",
    "aggregation",
    "out_of_scope",
    "greeting",
    "feedback",
    "chitchat",
    "vu_vo",
)


@pytest.mark.parametrize("intent", _CANONICAL_INTENTS)
def test_every_canonical_intent_has_topn_default(intent: str) -> None:
    """Every taxonomy intent must have an explicit per-intent value
    (no silent fallback to global default — which would defeat the
    whole point of per-intent tuning)."""
    from ragbot.shared.constants import DEFAULT_RERANK_TOP_N_BY_INTENT

    assert intent in DEFAULT_RERANK_TOP_N_BY_INTENT, (
        f"intent {intent!r} missing from DEFAULT_RERANK_TOP_N_BY_INTENT"
    )
    assert DEFAULT_RERANK_TOP_N_BY_INTENT[intent] > 0


@pytest.mark.parametrize("intent", _CANONICAL_INTENTS)
def test_every_canonical_intent_has_context_cap_default(intent: str) -> None:
    from ragbot.shared.constants import DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT

    assert intent in DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT
    assert DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT[intent] > 0


# -- mmr_similarity_threshold_by_intent (260525 Bug #10) ---------------------


def _resolve_mmr_thresh(
    intent: str,
    by_intent_cfg: object,
    global_fallback: float,
) -> tuple[float, bool]:
    """Mirror of mmr_dedup resolver in query_graph.py."""
    if isinstance(by_intent_cfg, dict) and intent in by_intent_cfg:
        try:
            return (float(by_intent_cfg[intent]), True)
        except (TypeError, ValueError):
            return (global_fallback, False)
    return (global_fallback, False)


def test_aggregation_intent_loosens_mmr_threshold() -> None:
    """Bug #10 reproducer — aggregation must get 0.98 not the default 0.88.

    The row-shape CSV chunks with the same column header but different
    data values (e.g. multiple "1499000" rows) ARE semantically distinct
    even though their embeddings are template-similar. Loosening the
    threshold lets them survive MMR dedup.
    """
    by_intent = {"factoid": 0.88, "aggregation": 0.98}
    thresh, override = _resolve_mmr_thresh("aggregation", by_intent, 0.88)
    assert thresh == 0.98
    assert override is True


def test_factoid_intent_keeps_default_mmr_threshold() -> None:
    by_intent = {"factoid": 0.88, "aggregation": 0.98}
    thresh, override = _resolve_mmr_thresh("factoid", by_intent, 0.88)
    assert thresh == 0.88
    assert override is True


def test_unknown_intent_falls_back_to_global_mmr_default() -> None:
    by_intent = {"factoid": 0.88, "aggregation": 0.98}
    thresh, override = _resolve_mmr_thresh("totally_unknown", by_intent, 0.88)
    assert thresh == 0.88
    assert override is False


def test_default_constant_aggregation_loosens_threshold() -> None:
    """Module-level default dict must boost aggregation above the global
    DEFAULT_MMR_SIMILARITY_THRESHOLD (0.88)."""
    from ragbot.shared.constants import (
        DEFAULT_MMR_SIMILARITY_THRESHOLD,
        DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT,
    )

    assert (
        DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT["aggregation"]
        > DEFAULT_MMR_SIMILARITY_THRESHOLD
    ), (
        "aggregation must get a LOOSER MMR threshold than the default — "
        "this is the whole point of the Bug #10 fix."
    )


@pytest.mark.parametrize("intent", _CANONICAL_INTENTS)
def test_every_canonical_intent_has_mmr_threshold_default(intent: str) -> None:
    from ragbot.shared.constants import DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT

    assert intent in DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT
    val = DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT[intent]
    assert 0.0 < val <= 1.0, (
        f"intent {intent!r} mmr threshold {val} must be in (0.0, 1.0]"
    )
