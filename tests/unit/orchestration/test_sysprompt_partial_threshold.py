"""Sysprompt partial-ground threshold constant — value pin.

The context-aware refusal template embeds a reranker-score floor that
separates the PARTIAL (answer with grounding qualifier) and WEAK
(answer with uncertainty caveat) branches. The bot owner tunes this
floor by editing their copy of the template; the platform pins the
default in ``shared/constants.py`` so the language pack, the template
constant, and the bot-limits schema agree on one source of truth.

This test pins three things:

1. The default value (0.20). A silent change here re-grades every bot
   row that uses the reference template.
2. The value sits in the closed [0.0, 1.0] interval (the reranker
   score domain).
3. The constant is included in ``shared/constants.__all__`` so callers
   importing the module surface receive the symbol.
"""

from __future__ import annotations

import ragbot.shared.constants as constants_module
from ragbot.shared.constants import DEFAULT_PARTIAL_GROUND_THRESHOLD


# The value carries semantic weight: 0.20 sits just above the cliff
# absolute floor (0.15) so PARTIAL ⇒ at-or-above-floor-plus-margin and
# WEAK ⇒ admitted-by-cliff-but-below-margin. Changing this default
# without re-running the 90Q load test risks shifting REFUSE_GAP.
_EXPECTED_DEFAULT_PARTIAL_GROUND_THRESHOLD: float = 0.20


def test_default_partial_ground_threshold_is_pinned_to_known_value() -> None:
    """The canonical default MUST equal 0.20. Bumping this value
    requires a deliberate post-load-test verification — drift here
    silently re-grades every bot opted into the context-aware
    template."""
    assert DEFAULT_PARTIAL_GROUND_THRESHOLD == _EXPECTED_DEFAULT_PARTIAL_GROUND_THRESHOLD


def test_default_partial_ground_threshold_in_score_domain() -> None:
    """Reranker scores live in the closed [0.0, 1.0] interval. The
    floor must sit inside that interval so the comparison in the
    template is meaningful."""
    assert 0.0 <= DEFAULT_PARTIAL_GROUND_THRESHOLD <= 1.0


def test_default_partial_ground_threshold_is_float_type() -> None:
    """Pin the type so accidental coercion to ``int`` (which would
    truncate at the cliff floor) cannot ship silently."""
    assert isinstance(DEFAULT_PARTIAL_GROUND_THRESHOLD, float)


def test_constant_is_exported_in_shared_all() -> None:
    """``from ragbot.shared.constants import *`` must surface the new
    symbol so downstream callers (admin tooling, runbooks) can import
    via the public module API."""
    assert "DEFAULT_PARTIAL_GROUND_THRESHOLD" in constants_module.__all__
