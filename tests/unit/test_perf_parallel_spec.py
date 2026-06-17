"""Living-spec tests for Q4+Q5+Q6 parallelisation.

These are skip-marked placeholders that serve as a TODO list for the next
agent that lands the parallel-rewrite + parallel-cache + async-reflect
options described in:

    reports/MEGA_PERF_PARALLEL_Q4_Q5_Q6_SPEC_20260501.md

When the implementation lands, unskip the matching test and replace the
``pytest.skip(...)`` body with the real assertion. Each test name maps to
one of the 5 options ranked in §2 of the spec.
"""

import pytest


_SPEC_DOC = "reports/MEGA_PERF_PARALLEL_Q4_Q5_Q6_SPEC_20260501.md"


@pytest.mark.skip(
    reason=(
        "spec-only — to be unskipped after Option A (rewrite + multi_query "
        f"parallel) ships. See {_SPEC_DOC} §2 / §5."
    )
)
def test_q4_q5_q6_parallel_target_p95_under_15s() -> None:
    """Spec: post-Option-A, p95 latency on R-FRESH-style 75 OLD turns ≤ 15s.

    Reference: ``reports/MEGA_PERF_PARALLEL_Q4_Q5_Q6_SPEC_20260501.md`` §1
    measured baseline p95 = 22 106 ms. Option A target ≈ 1.0 s p95 saving;
    combined with Option D (cache + understand overlap, ~0.9 s) → projected
    post-ship p95 ≤ 19 000 ms; with Option C (reflect async, ~3.5 s) ≤
    15 500 ms. This test asserts the combined A+D+C land target.
    """
    pytest.skip("see spec doc")


@pytest.mark.skip(
    reason=(
        "spec-only — Option A unit test placeholder. Will move to "
        "tests/unit/test_rewrite_multiquery_parallel.py on land."
    )
)
def test_option_a_rewrite_and_multi_query_run_concurrently() -> None:
    """Spec: rewrite LLM + multi_query LLM fire concurrently via gather."""
    pytest.skip("see spec doc §5")


@pytest.mark.skip(
    reason=(
        "spec-only — Option D unit test placeholder. Will move to "
        "tests/unit/test_cache_understand_overlap.py on land."
    )
)
def test_option_d_cache_lookup_and_understand_overlap_with_cancel_on_hit() -> None:
    """Spec: understand_query LLM started concurrently with cache embed.

    On cache hit the understand_query task MUST be cancelled (not awaited)
    so we don't pay for an LLM call we don't need.
    """
    pytest.skip("see spec doc §2 Option D")


@pytest.mark.skip(
    reason=(
        "spec-only — Option C unit test placeholder. Will move to "
        "tests/unit/test_reflect_async_does_not_block.py on land."
    )
)
def test_option_c_reflect_async_does_not_block_user_response() -> None:
    """Spec: when reflect path = ACCEPT, worker returns before reflect done."""
    pytest.skip("see spec doc §2 Option C")


@pytest.mark.skip(
    reason=(
        "spec-only — Option C edge case. Reflect=RETRY MUST stay on the "
        "critical path; we cannot ship answer X then mutate to answer Y."
    )
)
def test_option_c_reflect_retry_remains_synchronous() -> None:
    """Spec: reflect-RETRY edge case is NOT async-fired."""
    pytest.skip("see spec doc §2 Option C / §7")


@pytest.mark.skip(
    reason=(
        "spec-only — Option B placeholder. Audit: multi_query fanout "
        "semaphore is independent of grade/rerank semaphore."
    )
)
def test_option_b_multi_query_fanout_uses_dedicated_semaphore() -> None:
    """Spec: 5 mocked hybrid_search calls run truly in parallel."""
    pytest.skip("see spec doc §2 Option B")


@pytest.mark.skip(
    reason=(
        "spec-only — Option E DEFERRED. Streaming requires chunk-aware "
        "guardrail redesign; ship plan in a future T2 sprint."
    )
)
def test_option_e_streaming_first_token_under_900ms() -> None:
    """Spec: TTFT ≤ 900 ms — DEFERRED pending guardrail redesign."""
    pytest.skip("see spec doc §2 Option E")
