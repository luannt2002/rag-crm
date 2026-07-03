"""Step-2 (002 cluster C): speculative-hit must NOT override query composition.

Root cause: the speculative keep-branch returns from retrieve BEFORE the
decompose/multi-query fan-out — so sub-queries produced upstream were created
but NEVER retrieved (evidence: L-072 comparison missed the second brand's row;
L-014/L-005 comparison legs missing). Composition-aware rule: when decompose
produced ≥2 sub_queries, the speculative single-raw-query result set cannot
serve them — fall through to the fan-out.

Pin style: source-level (mirrors test_grounding_confirmed_action) + pure
predicate behavioral pin.
"""
from __future__ import annotations

import inspect


def test_keep_predicate_is_composition_aware() -> None:
    from ragbot.orchestration.nodes import retrieve as r

    src = inspect.getsource(r)
    # the keep decision must consult decompose state before returning
    i_keep = src.find("_decide_keep_speculative(")
    assert i_keep != -1
    window = src[max(0, i_keep - 1500):i_keep + 1500]
    assert "sub_queries" in window, (
        "speculative keep must check sub_queries (decompose) before short-circuiting"
    )


def test_decomposed_state_never_keeps_speculative() -> None:
    """Behavioral pin on the pure helper: ≥2 sub_queries ⇒ keep must be False
    regardless of cosine similarity."""
    from ragbot.orchestration.nodes.retrieve import _speculative_keep_allowed

    assert _speculative_keep_allowed(sub_queries=[]) is True
    assert _speculative_keep_allowed(sub_queries=["a"]) is True  # 1 = not decomposed
    assert _speculative_keep_allowed(sub_queries=["giá A", "giá B"]) is False
    assert _speculative_keep_allowed(sub_queries=["a", "b", "c"]) is False
