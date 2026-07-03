"""Step-1 (002 cluster A): condense-gate DRIFT — the 2026-05-27 fix ("first
follow-up triggers condense", skip only when len(history) < 2) landed ONLY in
the legacy condense_question node; the MERGED understand_query node kept strict
`>` (needs ≥3 messages) and production runs the merged path → coreference dead
on turn-2 of EVERY conversation (evidence: luannt100 L-082/085/087; spa S-064).

Fix = ONE pure predicate shared by both nodes (policy-drift class killer).
"""
from __future__ import annotations

import inspect

from ragbot.shared.condense_gate import has_meaningful_history
from ragbot.shared.constants import (
    DEFAULT_CONDENSE_MIN_HISTORY_CHARS,
    DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
)


def _msgs(*contents: str) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": c}
            for i, c in enumerate(contents)]


def test_first_followup_two_messages_triggers() -> None:
    """THE bug: turn-2 has exactly [user_T1, bot_T1] = 2 messages. The 2026-05-27
    semantics REQUIRE the gate to fire here (>= min_turns, not >)."""
    h = _msgs("x" * 60, "y" * 60)  # 2 msgs, 120 chars >= 100
    assert has_meaningful_history(
        h, min_turns=DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
        min_chars=DEFAULT_CONDENSE_MIN_HISTORY_CHARS) is True


def test_single_message_or_short_history_skips() -> None:
    assert has_meaningful_history(
        _msgs("x" * 200), min_turns=2, min_chars=100) is False  # 1 msg
    assert has_meaningful_history(
        _msgs("ab", "cd"), min_turns=2, min_chars=100) is False  # 4 chars
    assert has_meaningful_history([], min_turns=2, min_chars=100) is False
    assert has_meaningful_history(None, min_turns=2, min_chars=100) is False


def test_both_nodes_share_the_single_predicate() -> None:
    """Drift-proof pin: BOTH implementations must call the shared helper —
    a second hand-rolled copy of this predicate is the root cause class."""
    from ragbot.orchestration.nodes import condense_question, understand

    for mod in (understand, condense_question):
        src = inspect.getsource(mod)
        assert "has_meaningful_history(" in src, mod.__name__
    # the strict-'>' hand-rolled form must be gone from understand
    u_src = inspect.getsource(understand)
    assert "len(history) > DEFAULT_CONDENSE_MIN_HISTORY_TURNS" not in u_src
