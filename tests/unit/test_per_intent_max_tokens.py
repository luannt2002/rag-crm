"""T2-CostPerf — sysprompt v4 concision intent caps (2026-05-01).

Mission: shrink avg answer length from ~378 → ~150 chars on the
load-test bot. The lever is per-intent ``max_tokens`` cap. Tests below
pin the v4 spec values so a future regression cannot silently widen
the cap back to the v3 (450) value.

Spec (mission CRIT-2):
    chitchat = 80
    greeting = 60
    factoid  = 300
    comparison = 400
    default  = 250

Backward compat: missing intent → ``default`` key (= 250), never raises.

Strategy/DI: lookup table lives in ``shared/constants.py``. Bot owner
overrides via ``pipeline_config.generate_max_tokens_by_intent``. No
provider/model literal touched.
"""
from __future__ import annotations

import pytest

from ragbot.shared.constants import DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT


# ---------------------------------------------------------------------------
# v4 spec pin — exact-value tests for each lever the mission specified.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "intent,expected",
    [
        ("chitchat", 80),
        ("greeting", 60),
        ("factoid", 300),
        ("comparison", 400),
        ("default", 250),
    ],
)
def test_intent_max_tokens_pin(intent: str, expected: int) -> None:
    """Each spec intent caps at exactly the documented value.

    A future change widening any of these silently undoes the concision
    win we just shipped. Force a deliberate update of this test if the
    cap moves.
    """
    assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT[intent] == expected, (
        f"intent-cap drift: intent={intent} expected={expected} "
        f"actual={DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT[intent]} — "
        "either update test deliberately or restore the cap."
    )


# ---------------------------------------------------------------------------
# Backward-compat lookup — missing/empty/unknown intent falls back to
# the ``default`` key, never raises KeyError. Mirrors the predicate used
# inside the generate node ``state.get('intent') or 'default'``.
# ---------------------------------------------------------------------------
def test_missing_intent_falls_back_to_default_250() -> None:
    """Unknown intent classifier output must not crash the cap lookup."""
    intent_map = DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT

    def _lookup(intent: str) -> int:
        return intent_map.get(intent, intent_map["default"])

    # Empty string (intent classifier returned nothing).
    assert _lookup("") == 250
    # Unknown intent (e.g. classifier added a new label we don't map yet).
    assert _lookup("brand_new_intent_label") == 250
    # None-like sentinel (string).
    assert _lookup("none") == 250


# ---------------------------------------------------------------------------
# Concision invariant — short conversational intents must stay STRICTLY
# below the factoid cap so the perf + UX win persists across edits.
# ---------------------------------------------------------------------------
def test_short_intents_below_factoid() -> None:
    """If anyone bumps a short-intent value above factoid we lose the
    concision win on conversational turns. Note: dead-key test labels
    (off_topic / hallucination_trap) dropped — classifier never emits
    them; out_of_scope is the live equivalent."""
    factoid_cap = DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT["factoid"]
    short_intents = ("greeting", "chitchat", "vu_vo", "out_of_scope")
    for intent in short_intents:
        assert DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT[intent] < factoid_cap, (
            f"intent={intent} must stay below factoid={factoid_cap}"
        )
