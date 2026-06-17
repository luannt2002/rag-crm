"""Issue 6 regression — weight_numbers must classify 2+ numeric entities as complex.

Alembic migration ``20260515_0103_tune_query_complexity_weight_numbers.py``
raises ``query_complexity.weight_numbers`` from 0.3 to 0.6 so compound
queries that reference two or more integer entities reliably cross the
1.2 complexity threshold and trigger the decompose path.

This test exercises the **pure scorer** with the post-tune weight passed
through the injected ``config_getter``. It does not couple to the live
DB seed (covered separately by an alembic smoke test). If someone tunes
the weight further, update the values here and the alembic comment in
lock-step.

Domain-neutral: examples use abstract identifiers and structural words
that appear across many tenants (legal "Điều", spec "Section",
spec/order "item"). No customer/brand names.
"""

from __future__ import annotations

import pytest

from ragbot.orchestration.nodes.query_complexity import classify_query_complexity
from ragbot.shared.constants import (
    DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON,
    DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
    DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
    DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
)


_POST_TUNE_GETTER_BASE: dict[str, object] = {
    "query_complexity.weight_comma": DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA,
    "query_complexity.weight_conjunction": DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION,
    # The tune under test:
    "query_complexity.weight_numbers": 0.6,
    "query_complexity.weight_question": DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION,
    "query_complexity.length_normalizer": DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER,
    "query_complexity.complexity_threshold": DEFAULT_QUERY_COMPLEXITY_THRESHOLD,
    "query_complexity.conjunctions": DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON,
}


def _getter(key, default):
    return _POST_TUNE_GETTER_BASE.get(key, default)


@pytest.mark.parametrize(
    "query, expected_label, scenario",
    [
        # Single-entity factoid stays simple (no decompose overhead wasted).
        ("Điều 36", "simple", "single entity legal"),
        ("Section 12", "simple", "single entity EN spec"),
        ("phí 100 đồng là bao nhiêu", "simple", "price (1 number, non-entity)"),
        # 2-entity compound queries should flip to complex.
        ("Điều 38 và 3", "complex", "2 entities + conjunction"),
        ("Section 12 and 7", "complex", "2 entities EN + conjunction"),
        # 3-entity list compound queries should flip to complex.
        ("Điều 7, 29, 51", "complex", "3 entities comma list"),
        ("Section 1, 2, 3", "complex", "3 entities EN comma list"),
        # Pure non-numeric question stays simple.
        ("hello", "simple", "no number"),
    ],
)
def test_compound_numeric_query_routes_to_complex(query, expected_label, scenario):
    label, score = classify_query_complexity(query, config_getter=_getter)
    assert label == expected_label, (
        f"{scenario!r} mis-classified: query={query!r} score={score:.2f} "
        f"label={label} (expected {expected_label}). Threshold = 1.2 with "
        "weight_numbers=0.6 (post-tune)."
    )
