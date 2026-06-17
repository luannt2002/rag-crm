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
    src = inspect.getsource(qg.build_graph)
    # Find the condense_question definition body
    assert "async def condense_question" in src
    # 2026-06-13 zero-hardcode: the literal ``2`` was lifted into
    # ``DEFAULT_CONDENSE_MIN_HISTORY_TURNS`` (= 2). The contract is unchanged —
    # the runtime gate must still use ``<`` (strict) so the FIRST follow-up
    # (len==2) triggers, never ``<=`` (which would swallow it). Pin both the
    # operator+constant form AND the constant value.
    from ragbot.shared.constants import DEFAULT_CONDENSE_MIN_HISTORY_TURNS
    assert DEFAULT_CONDENSE_MIN_HISTORY_TURNS == 2
    assert "len(history) < DEFAULT_CONDENSE_MIN_HISTORY_TURNS" in src, (
        "condense_question threshold reverted from `< MIN_HISTORY_TURNS`; "
        "first follow-up loses standalone-question rewriting again."
    )
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
    src = inspect.getsource(qg.build_graph)
    # Locate rewrite() node code
    assert "async def rewrite" in src
    # Hallmark of the new threading logic
    assert "Conversation context (last turns)" in src, (
        "rewrite node missing history-aware user content; multi-turn "
        "pronouns ('có ưu đãi không' after T1 entity) will not resolve."
    )
    # Also assert it reads conversation_history (not just query)
    assert 'state.get("conversation_history"' in src
