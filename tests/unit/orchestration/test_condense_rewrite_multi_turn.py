"""Pin test: condense + rewrite multi-turn context propagation.

Before 2026-05-27:
  - condense_question SKIPPED when len(history) <= 2 (i.e. exactly 1 turn pair)
  - rewrite NEVER received history → standalone-query was lost on follow-ups

After fix:
  - condense_question fires for len(history) >= 2 (first follow-up triggers)
  - rewrite receives last 4 history messages → pronoun resolution works
"""

from __future__ import annotations

import inspect

from ragbot.orchestration import query_graph as qg


def test_condense_threshold_is_less_than_two_not_less_equal_two() -> None:
    """condense_question source must use ``< 2`` so first follow-up triggers.

    Pre-fix: ``if not history or len(history) <= 2: return {}``
    Post-fix: ``if not history or len(history) < 2: return {}``

    Source-level pin so a future refactor cannot silently revert.
    """
    # condense_question's body now lives in its own node module (build_graph
    # binds it via functools.partial). Pin the threshold at the source of truth.
    from ragbot.orchestration.nodes.condense_question import (
        condense_question as _condense_question_node,
    )
    src = inspect.getsource(_condense_question_node)
    # Find the condense_question definition body
    assert "async def condense_question" in src
    # 2026-07-04 (truth-audit 002 cluster A): the predicate moved into the
    # SHARED pure helper ``shared/condense_gate.has_meaningful_history`` —
    # the same 2026-05-27 semantics (skip only when len(history) < min_turns,
    # so the FIRST follow-up with len==2 triggers), now drift-proof because
    # the merged understand node consumes the SAME helper (the old hand-rolled
    # strict `>` copy there is what killed turn-2 coreference in production).
    # Pin: node must call the shared helper; helper must keep `< min_turns`.
    from ragbot.shared.condense_gate import has_meaningful_history
    from ragbot.shared.constants import DEFAULT_CONDENSE_MIN_HISTORY_TURNS
    assert "has_meaningful_history(" in src
    helper_src = inspect.getsource(has_meaningful_history)
    assert "len(history) < min_turns" in helper_src, (
        "shared gate reverted: first follow-up (len==2) would lose condense"
    )
    # behavioral pin: 2-message history (>=100 chars) MUST fire
    assert has_meaningful_history(
        [{"role": "user", "content": "x" * 60}, {"role": "assistant", "content": "y" * 60}],
        min_turns=DEFAULT_CONDENSE_MIN_HISTORY_TURNS, min_chars=100,
    ) is True
    assert DEFAULT_CONDENSE_MIN_HISTORY_TURNS == 2
    # The strict runtime gate must NOT use `<=` (would swallow the first
    # follow-up). Docstring may mention `<= 2` once as historical context.
    assert src.count("len(history) <= DEFAULT_CONDENSE_MIN_HISTORY_TURNS") == 0, (
        "stale `<=` threshold present; first follow-up would be swallowed."
    )


def test_rewrite_node_passes_history_into_user_content() -> None:
    """rewrite must thread last history messages into LLM user content.

    Before fix: ``messages = [{system}, {user: query}]`` — no history.
    After fix: when history exists, user content is
        ``"Conversation context (last turns):\\n...\\nCurrent query: ..."``
    """
    # rewrite's body now lives in its own node module (build_graph binds it
    # via functools.partial). Pin the history-threading logic at its source.
    from ragbot.orchestration.nodes.rewrite import rewrite as _rewrite_node
    src = inspect.getsource(_rewrite_node)
    # Locate rewrite() node code
    assert "async def rewrite" in src
    # Hallmark of the new threading logic
    assert "Conversation context (last turns)" in src, (
        "rewrite node missing history-aware user content; multi-turn "
        "pronouns ('có ưu đãi không' after T1 entity) will not resolve."
    )
    # Also assert it reads conversation_history (not just query)
    assert 'state.get("conversation_history"' in src
