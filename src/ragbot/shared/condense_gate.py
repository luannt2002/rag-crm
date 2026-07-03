"""Condense-gate predicate — SINGLE source of truth for "is history meaningful
enough to condense the follow-up question?".

WHY one shared helper: the 2026-05-27 threshold fix ("the FIRST follow-up must
trigger condense — turn-2 history is exactly [user_T1, bot_T1] = 2 messages,
which is where pronoun coreference matters most") was applied to the legacy
condense node only, while the merged understand node kept a hand-rolled strict
`>` copy — so production (merged path) never got the fix and every turn-2
"nó/cái đó" lost its antecedent (truth-audit 002 cluster A; evidence:
specs/002-deepdebug-luannt/evidence/debug_findings.json). Two hand-rolled
copies of one policy predicate = the drift class this module kills.

Pure function: no state, no I/O, thresholds injected (SSoT constants at the
call sites) — trivially testable, reusable by any future condense consumer.
"""
from __future__ import annotations


def has_meaningful_history(
    history: list[dict] | None,
    *,
    min_turns: int,
    min_chars: int,
) -> bool:
    """True when *history* is substantial enough to run condense.

    Semantics (2026-05-27): fire from ``len(history) >= min_turns`` — with the
    default of 2 this includes the very first follow-up ([user_T1, bot_T1]).
    The char floor keeps one-word greeting exchanges from paying the condense
    LLM cost.
    """
    if not history or len(history) < min_turns:
        return False
    return sum(len(m.get("content", "")) for m in history) >= min_chars


__all__ = ["has_meaningful_history"]
