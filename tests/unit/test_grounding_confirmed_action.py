"""A1: symmetric grounding gate — when the judge CONFIRMS an answer is
ungrounded, a per-bot ``grounding_confirmed_action="block"`` substitutes the
bot's oos_answer_template instead of shipping the fabricated answer with only a
flag. Default stays "observe" (legacy ship-and-flag) so no bot's refuse-rate
changes without an explicit, measured opt-in.
"""
from __future__ import annotations

import inspect


def test_grounding_confirmed_action_constants() -> None:
    from ragbot.shared.constants import (
        DEFAULT_GROUNDING_CONFIRMED_ACTION,
        GROUNDING_CONFIRMED_ACTION_BLOCK,
        GROUNDING_CONFIRMED_ACTION_OBSERVE,
    )

    assert GROUNDING_CONFIRMED_ACTION_OBSERVE == "observe"
    assert GROUNDING_CONFIRMED_ACTION_BLOCK == "block"
    # Default MUST be observe — flipping to block system-wide without a
    # calibrated threshold would over-refuse (the grounding judge is
    # deliberately false-positive biased). Owners opt into block per-bot.
    assert DEFAULT_GROUNDING_CONFIRMED_ACTION == GROUNDING_CONFIRMED_ACTION_OBSERVE


def test_guard_output_has_confirmed_block_branch() -> None:
    """The confirmed-ungrounded path must, under the block action, return the
    bot oos template + answer_type=blocked (sacred-#10-safe refusal), mirroring
    the regex-block branch. Guards the fix against silent removal."""
    from ragbot.orchestration.nodes import guard_output

    src = inspect.getsource(guard_output)
    assert 'grounding_confirmed_action' in src
    assert 'GROUNDING_CONFIRMED_ACTION_BLOCK' in src
    # The block branch must substitute the bot template + mark blocked, not
    # override with app text.
    assert '"answer": _oos_template' in src
    assert '"answer_type": "blocked"' in src


def test_confirmed_block_predicate() -> None:
    """Mirror the node predicate: only the exact 'block' action blocks; the
    default 'observe' ships (flag-only)."""
    from ragbot.shared.constants import (
        DEFAULT_GROUNDING_CONFIRMED_ACTION,
        GROUNDING_CONFIRMED_ACTION_BLOCK,
    )

    def _blocks(action: str) -> bool:
        return action == GROUNDING_CONFIRMED_ACTION_BLOCK

    assert _blocks("block") is True
    assert _blocks(DEFAULT_GROUNDING_CONFIRMED_ACTION) is False  # observe ships
    assert _blocks("anything_else") is False
