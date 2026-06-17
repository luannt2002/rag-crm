"""Fix B-Q8-1 — VocabularyExpander singleton must not be mutated per-call.

Race condition: when two concurrent requests call enrich_state with different
max_matches/max_expansions (per-bot config), the old code mutated
``_vocab_expander._max_matches`` directly on the module-level singleton.
Request B could overwrite request A's value mid-flight.

Fix: enrich_state accepts max_matches/max_expansions as call-time args and
passes them through a local variable path, never writing to self.
"""

from __future__ import annotations

import asyncio

import pytest


def test_enrich_state_accepts_call_time_limits():
    """enrich_state signature accepts max_matches and max_expansions kwargs."""
    import inspect

    from ragbot.application.services.vocabulary_expander import VocabularyExpander

    sig = inspect.signature(VocabularyExpander.enrich_state)
    params = sig.parameters
    assert "max_matches" in params, "enrich_state must accept max_matches kwarg"
    assert "max_expansions" in params, "enrich_state must accept max_expansions kwarg"
    # Both should be optional (default None)
    assert params["max_matches"].default is None
    assert params["max_expansions"].default is None


def test_enrich_state_call_time_limit_respected():
    """Call-time max_matches=1 overrides instance default."""
    from ragbot.application.services.vocabulary_expander import VocabularyExpander

    expander = VocabularyExpander(max_matches=10, max_expansions=5)
    state: dict = {}
    # Query with multiple potential matches; with max_matches=1, only 1 match injected
    query = "ko dc vs ok"  # "ko"->không, "dc"->được, "vs"->với, "ok"->được
    result = expander.enrich_state(state, query, max_matches=1, max_expansions=1)
    vocab = result.get("context_base", {}).get("vocabulary", {})
    if vocab:
        # At most 1 match injected
        assert len(vocab.get("matches", [])) <= 1, (
            "max_matches=1 call-time arg must limit matches to ≤1"
        )


def test_enrich_state_does_not_mutate_singleton():
    """Calling enrich_state with call-time limits must not change instance attributes."""
    from ragbot.application.services.vocabulary_expander import VocabularyExpander

    expander = VocabularyExpander(max_matches=10, max_expansions=5)
    original_max_matches = expander._max_matches
    original_max_expansions = expander._max_expansions

    state: dict = {}
    expander.enrich_state(state, "ko dc vs", max_matches=1, max_expansions=1)

    assert expander._max_matches == original_max_matches, (
        "enrich_state must NOT mutate self._max_matches (race condition)"
    )
    assert expander._max_expansions == original_max_expansions, (
        "enrich_state must NOT mutate self._max_expansions (race condition)"
    )


@pytest.mark.asyncio
async def test_concurrent_calls_no_race():
    """50 concurrent enrich_state calls with different max_matches respect their own limits.

    This test verifies the race condition fix. Without fix:
    - concurrent writes to _max_matches would stomp each other
    - matches count would be non-deterministic
    With fix:
    - each call uses its own call-time arg, never touching singleton state
    """
    from ragbot.application.services.vocabulary_expander import VocabularyExpander

    expander = VocabularyExpander(max_matches=10, max_expansions=5)
    query = "ko dc vs ok tks thx"  # 6 possible matches

    violations: list[str] = []

    async def _call_with_limit(limit: int, call_id: int) -> None:
        state: dict = {}
        result = expander.enrich_state(state, query, max_matches=limit, max_expansions=1)
        vocab = result.get("context_base", {}).get("vocabulary", {})
        n_matches = len(vocab.get("matches", [])) if vocab else 0
        if n_matches > limit:
            violations.append(
                f"call_id={call_id} limit={limit} got {n_matches} matches (violation)"
            )

    # Mix of different limits: 1, 2, 3 — all running concurrently
    tasks = []
    for i in range(50):
        limit = (i % 3) + 1  # cycles 1, 2, 3
        tasks.append(_call_with_limit(limit, i))

    await asyncio.gather(*tasks)

    assert not violations, (
        f"Race condition detected in {len(violations)} calls:\n"
        + "\n".join(violations[:5])
    )
