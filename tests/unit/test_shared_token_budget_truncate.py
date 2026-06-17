"""Unit tests for ``ragbot.shared.token_budget.truncate_to_token_budget``.

The function is RAG-Anything M22 — a bounded-list helper for any node
that grows context until a token budget is reached. Tests cover the
exhaustive contract documented in the helper's docstring:

  1. Empty iterable -> empty list (no estimator call, no crash).
  2. Budget == 0 with at least one item -> head element returned (the
     "always include head" rule).
  3. Estimator returning 0 for all items -> all items returned (nothing
     ever exceeds budget; loop runs to exhaustion).
  4. Mid-stream over-budget -> truncate at last fit; stops iterating.
  5. Generator input -> consumed lazily; remaining items untouched.
  6. Negative estimator output is clamped to >= 0 (defensive).
  7. Output is a fresh list (not the input) — caller may mutate.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ragbot.shared.token_budget import truncate_to_token_budget


def test_empty_iterable_returns_empty_list() -> None:
    """Zero-input → zero-output. Estimator must not be called."""
    estimator_calls: list[object] = []

    def estimator(item: object) -> int:
        estimator_calls.append(item)
        return 1

    result = truncate_to_token_budget([], budget=1024, token_estimator=estimator)

    assert result == []
    assert estimator_calls == []


def test_budget_zero_still_returns_head_element() -> None:
    """Budget=0 + non-empty input → head retained (graceful rule)."""
    items = ["alpha", "beta", "gamma"]

    result = truncate_to_token_budget(items, budget=0, token_estimator=lambda _x: 100)

    # "Always include head" — even though head alone exceeds budget.
    assert result == ["alpha"]


def test_zero_token_estimator_returns_all_items() -> None:
    """Estimator returning 0 → no item ever exceeds → return everything."""
    items = list(range(50))

    result = truncate_to_token_budget(items, budget=10, token_estimator=lambda _x: 0)

    assert result == items
    assert len(result) == 50


def test_mid_stream_over_budget_truncates_at_last_fit() -> None:
    """Cumulative limit honoured; iteration stops on first overshoot."""
    items = ["a", "b", "c", "d", "e"]
    # Per-item tokens: 3 each ; budget=10
    # i=0 head: kept (accumulated=3). i=1: 6 ≤ 10 keep. i=2: 9 ≤ 10 keep.
    # i=3: 12 > 10 → break.
    result = truncate_to_token_budget(
        items, budget=10, token_estimator=lambda _x: 3
    )

    assert result == ["a", "b", "c"]


def test_generator_input_consumed_lazily() -> None:
    """Generators should only be advanced as far as needed."""
    consumed: list[int] = []

    def gen() -> Iterator[int]:
        for i in range(100):
            consumed.append(i)
            yield i

    # Per-item cost = 10. Head (i=0) accumulates to 10.
    # i=1..3: 20, 30, 40 ≤ 40 → keep. i=4: 50 > 40 → break.
    # Result = 4 items (indices 0..3).
    result = truncate_to_token_budget(gen(), budget=40, token_estimator=lambda _x: 10)

    assert result == [0, 1, 2, 3]
    # Generator advanced one beyond the kept window to detect overflow, then
    # stopped. We must NOT have walked the full 100.
    assert len(consumed) <= 5, f"generator over-consumed: {len(consumed)}"


def test_negative_estimator_output_clamped_to_zero() -> None:
    """Buggy estimator returning -N must not enlarge the effective budget."""
    items = list(range(5))

    # Ints estimate as -100 (clamped to 0) → all 5 fit (accumulated stays 0).
    # "BIG" estimates as 50 → 0+50 > 10 → rejected.
    result = truncate_to_token_budget(
        items + ["BIG"], budget=10, token_estimator=lambda x: -100 if isinstance(x, int) else 50
    )

    assert result == [0, 1, 2, 3, 4]


def test_result_is_fresh_list_not_input() -> None:
    """Caller may mutate the result safely."""
    items = ["x", "y", "z"]

    result = truncate_to_token_budget(items, budget=100, token_estimator=lambda _x: 1)

    assert result == ["x", "y", "z"]
    result.append("appended")
    # Mutation on result must not bleed into input.
    assert items == ["x", "y", "z"]


def test_estimator_called_in_order_once_per_inspected_item() -> None:
    """Performance guarantee: estimator is O(1) per item; never re-called."""
    seen: list[int] = []

    def estimator(item: int) -> int:
        seen.append(item)
        return 4

    items = [10, 20, 30, 40, 50, 60]
    # Budget=12: head (=4) + i=1 (8) + i=2 (12) → keep 3. i=3 (16) → break.
    result = truncate_to_token_budget(items, budget=12, token_estimator=estimator)

    assert result == [10, 20, 30]
    # Estimator visited head + 2 fits + 1 reject = 4 items, in order.
    assert seen == [10, 20, 30, 40]


@pytest.mark.parametrize(
    ("budget", "tokens_each", "expected_count"),
    [
        (0, 1, 1),  # head-only rule (head added despite over-budget)
        (1, 1, 1),  # head accumulates to 1; next would push to 2 > 1
        (2, 1, 2),  # head=1, then +1=2 ≤ 2; next 3 > 2
        (10, 3, 3),  # head=3, +3=6, +3=9; next 12 > 10
        (100, 10, 10),  # 10 items × 10 each = 100 ≤ 100
    ],
)
def test_parametric_boundary_table(
    budget: int, tokens_each: int, expected_count: int
) -> None:
    """Boundary arithmetic — head-always-added + cumulative ≤ budget rule."""
    items = list(range(10))

    result = truncate_to_token_budget(
        items, budget=budget, token_estimator=lambda _x: tokens_each
    )

    assert len(result) == expected_count
