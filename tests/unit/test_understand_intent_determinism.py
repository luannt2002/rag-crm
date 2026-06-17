"""Fix B1-Q3 — _VALID_INTENTS must be an ordered list for deterministic fallback.

When the LLM JSON parse fails, understand_query iterates _VALID_INTENTS to find
the first match in the raw text. If _VALID_INTENTS is a Python set, hash
randomization makes the first match non-deterministic across processes. This
test verifies the ordered-list fix.
"""

from __future__ import annotations


def test_valid_intents_is_list():
    """_VALID_INTENTS must be a list, NOT a set, for deterministic iteration."""
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    assert isinstance(_VALID_INTENTS, list), (
        f"_VALID_INTENTS must be list for deterministic iteration, got {type(_VALID_INTENTS)}"
    )


def test_valid_intents_factoid_first():
    """'factoid' must be first in _VALID_INTENTS.

    The fallback text-scan picks the FIRST matching intent.
    'factoid' first → prefer retrieval over OOS on ambiguous responses
    (P18-1 design intent).
    """
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    assert len(_VALID_INTENTS) > 0, "_VALID_INTENTS must not be empty"
    assert _VALID_INTENTS[0] == "factoid", (
        f"First intent must be 'factoid' (prefer retrieval), got '{_VALID_INTENTS[0]}'"
    )


def test_valid_intents_contains_required_values():
    """All required intent values must be present."""
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    required = {"factoid", "multi_hop", "out_of_scope", "greeting"}
    missing = required - set(_VALID_INTENTS)
    assert not missing, f"_VALID_INTENTS missing required values: {missing}"


def test_fallback_scan_deterministic_same_input():
    """Fallback text-scan produces same intent on every call for same input.

    Simulates the logic in understand_query fallback loop:
      for cand in _VALID_INTENTS:
          if cand in raw_text.lower():
              intent = cand
              break
    """
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    raw_text = "This is a factoid question about out_of_scope topics."

    results = set()
    for _ in range(20):
        intent = "factoid"  # default
        for cand in _VALID_INTENTS:
            if cand in raw_text.lower():
                intent = cand
                break
        results.add(intent)

    assert len(results) == 1, (
        f"Fallback scan produced different intents across iterations: {results}. "
        "This indicates non-determinism — _VALID_INTENTS must be an ordered list."
    )
    # With ordered list and 'factoid' first: 'factoid' is in text and first → wins
    assert "factoid" in results


def test_fallback_scan_out_of_scope_only():
    """When only 'out_of_scope' matches, that intent is returned."""
    from ragbot.orchestration.query_graph import _VALID_INTENTS

    raw_text = "The query is out_of_scope for this domain."

    intent = "factoid"
    for cand in _VALID_INTENTS:
        if cand in raw_text.lower():
            intent = cand
            break

    assert intent == "out_of_scope"
