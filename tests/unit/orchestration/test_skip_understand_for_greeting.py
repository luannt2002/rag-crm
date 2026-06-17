"""Stream B3 — skip understand_query for greeting / short query.

Phase B GA latency optimisation: bypass the understand_query LLM call
when the user message is short (≤ N tokens) or matches one of the
configured greeting regex patterns. Saves ~1.5s per greeting turn;
intent is set directly to ``INTENT_GREETING``.

Tests in this module are pure source-level (no LangGraph boot, no DB) —
they encode the invariants of the early-exit gate at function level:

1. ``DEFAULT_SKIP_UNDERSTAND_FOR_GREETING`` is ``False`` (feature flag
   default OFF — legacy behaviour preserved until bot owner opts in).
2. The pure helper ``_understand_greeting_short_circuit`` returns the
   right reason for each branch:
   * feature flag OFF → None (no bypass, regardless of input)
   * short query (≤ N tokens) → "short"
   * greeting regex match → "greeting"
   * normal factual query → None (LLM understand still runs)
3. Greeting patterns cover VN + EN basics (chào / hi / cảm ơn / thanks /
   bye / good morning).
4. PLAN_LIMIT_SCHEMA wires the same defaults so per-bot override is
   resolvable.
5. Bad regex in bot config does not crash — degrades to LLM understand
   (graceful degrade).
6. Empty greeting_patterns list disables the regex branch but keeps
   token-count short-circuit active.
"""
from __future__ import annotations

from ragbot.orchestration.query_graph import (
    _understand_greeting_short_circuit,
)
from ragbot.shared.constants import (
    DEFAULT_GREETING_PATTERNS,
    DEFAULT_SKIP_UNDERSTAND_FOR_GREETING,
    DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS,
    INTENT_GREETING,
)


def _state(query: str, **pcfg: object) -> dict:
    """Build a minimal GraphState dict for the short-circuit helper.

    The helper only reads ``query`` + ``pipeline_config`` from state; no
    step_tracker / DB / LLM dependencies required.
    """
    return {"query": query, "pipeline_config": dict(pcfg)}


# ---------------------------------------------------------------------------
# 1. Feature-flag default — OFF preserves legacy behaviour.
# ---------------------------------------------------------------------------
def test_default_skip_understand_for_greeting_is_off() -> None:
    """The flag MUST default to False — flipping it to True silently
    changes pipeline behaviour for every bot that hasn't opted in.
    Phase B ships the gate dormant; bot owners opt in via plan_limits."""
    assert DEFAULT_SKIP_UNDERSTAND_FOR_GREETING is False, (
        "DEFAULT_SKIP_UNDERSTAND_FOR_GREETING must remain False — Phase B "
        "ships the gate dormant. Bot owner opts in via plan_limits."
    )


def test_short_circuit_returns_none_when_flag_off() -> None:
    """Flag OFF → helper returns None for every input, no matter how
    short or greeting-like the query is. Pipeline falls through to the
    LLM understand path."""
    # Default state (no pipeline_config override) → flag uses default OFF.
    assert _understand_greeting_short_circuit(_state("hi")) is None
    assert _understand_greeting_short_circuit(_state("chào")) is None
    assert _understand_greeting_short_circuit(_state("a")) is None
    # Explicit False also disables.
    assert (
        _understand_greeting_short_circuit(
            _state("hi", skip_understand_for_greeting=False),
        )
        is None
    )


# ---------------------------------------------------------------------------
# 2. Short-query branch — token-count short-circuit.
# ---------------------------------------------------------------------------
def test_short_query_branch_returns_short() -> None:
    """When flag is ON and query has ≤ skip_below_tokens whitespace tokens,
    helper returns "short" — first branch matched (cheaper than regex)."""
    state = _state(
        "what?",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
    )
    assert _understand_greeting_short_circuit(state) == "short"
    # Boundary: exactly N tokens still qualifies as short (≤, not <).
    state_boundary = _state(
        "one two three",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
    )
    assert _understand_greeting_short_circuit(state_boundary) == "short"


def test_short_query_branch_disabled_by_zero_threshold() -> None:
    """Threshold 0 disables the short-query branch entirely so only the
    regex branch fires. Lets bot owner depend purely on patterns."""
    state = _state(
        "one two",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=0,
        # Patterns won't match a non-greeting two-word query.
        understand_greeting_patterns=[],
    )
    assert _understand_greeting_short_circuit(state) is None


# ---------------------------------------------------------------------------
# 3. Greeting-regex branch — patterns matched case-insensitively.
# ---------------------------------------------------------------------------
def test_greeting_regex_branch_returns_greeting_vietnamese() -> None:
    """Vietnamese greeting "cảm ơn anh" (3+ tokens — past the short
    threshold) must still match the greeting regex branch when patterns
    are wired."""
    state = _state(
        "cảm ơn anh nhiều lắm nha",  # 6 tokens — past short branch.
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=list(DEFAULT_GREETING_PATTERNS),
    )
    assert _understand_greeting_short_circuit(state) == "greeting"


def test_greeting_regex_branch_returns_greeting_english() -> None:
    """English greeting "good morning everyone" past short branch should
    still match via the regex."""
    state = _state(
        "good morning everyone here",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=list(DEFAULT_GREETING_PATTERNS),
    )
    assert _understand_greeting_short_circuit(state) == "greeting"


def test_greeting_regex_branch_case_insensitive() -> None:
    """Patterns are anchored but matched IGNORECASE — uppercase /
    mixed-case greetings still trigger the branch."""
    state = _state(
        "HELLO world how are you",  # 5 tokens
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=list(DEFAULT_GREETING_PATTERNS),
    )
    assert _understand_greeting_short_circuit(state) == "greeting"


# ---------------------------------------------------------------------------
# 4. Normal factual query — no bypass.
# ---------------------------------------------------------------------------
def test_normal_factual_query_no_bypass() -> None:
    """A real factual query past the short threshold and without
    greeting markers must NOT short-circuit — pipeline runs the LLM
    understand path normally."""
    state = _state(
        "Điều 11 nghị định 168 quy định gì về vi phạm tốc độ?",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=list(DEFAULT_GREETING_PATTERNS),
    )
    assert _understand_greeting_short_circuit(state) is None


def test_empty_query_no_bypass() -> None:
    """Empty / whitespace-only queries return None so the LLM path can
    raise the usual missing-query error rather than the gate masking it."""
    state = _state(
        "",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
    )
    assert _understand_greeting_short_circuit(state) is None
    state_ws = _state(
        "   ",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
    )
    assert _understand_greeting_short_circuit(state_ws) is None


# ---------------------------------------------------------------------------
# 5. Graceful degrade — bad regex in bot config does not crash.
# ---------------------------------------------------------------------------
def test_invalid_regex_pattern_falls_back_to_llm() -> None:
    """Bot-owner-authored bad regex must not break the pipeline. The
    helper skips the bad pattern and either matches a later valid one
    or returns None (degrades to the LLM understand path)."""
    state = _state(
        "hello there my friend yes",  # 5 tokens, past short branch
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        # Bad regex (unbalanced paren) + a valid pattern after.
        understand_greeting_patterns=[r"(unclosed", r"^hello\b"],
    )
    # Valid pattern after the bad one still matches.
    assert _understand_greeting_short_circuit(state) == "greeting"
    # When the bad regex is the ONLY one, helper returns None.
    state_only_bad = _state(
        "hello there my friend yes",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=[r"(unclosed"],
    )
    assert _understand_greeting_short_circuit(state_only_bad) is None


# ---------------------------------------------------------------------------
# 6. Empty patterns list disables regex branch but keeps short branch.
# ---------------------------------------------------------------------------
def test_empty_patterns_list_disables_regex_only() -> None:
    """Empty list under understand_greeting_patterns turns off the regex
    branch entirely — short-query branch still fires."""
    # Short query — still short-circuits via token-count branch.
    state_short = _state(
        "hi",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=[],
    )
    assert _understand_greeting_short_circuit(state_short) == "short"
    # Long greeting — would have matched regex, but list is empty → None.
    state_long = _state(
        "good morning team how is everyone",
        skip_understand_for_greeting=True,
        understand_skip_below_tokens=3,
        understand_greeting_patterns=[],
    )
    assert _understand_greeting_short_circuit(state_long) is None


# ---------------------------------------------------------------------------
# 7. PLAN_LIMIT_SCHEMA wiring — defaults track constants.
# ---------------------------------------------------------------------------
def test_plan_limit_schema_default_tracks_constants() -> None:
    """``bot_limits.PLAN_LIMIT_SCHEMA`` must source its defaults from the
    same constants — otherwise per-bot override resolved against the
    schema default diverges from the system default."""
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA

    flag_entry = PLAN_LIMIT_SCHEMA["skip_understand_for_greeting"]
    assert flag_entry["type"] == "bool"
    assert flag_entry["default"] is DEFAULT_SKIP_UNDERSTAND_FOR_GREETING

    tokens_entry = PLAN_LIMIT_SCHEMA["understand_skip_below_tokens"]
    assert tokens_entry["type"] == "int"
    assert tokens_entry["default"] == DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS

    patterns_entry = PLAN_LIMIT_SCHEMA["understand_greeting_patterns"]
    assert patterns_entry["type"] == "list_str"
    assert tuple(patterns_entry["default"]) == tuple(DEFAULT_GREETING_PATTERNS)


# ---------------------------------------------------------------------------
# 8. Default patterns cover the documented greeting vocabulary.
# ---------------------------------------------------------------------------
def test_default_greeting_patterns_cover_vn_and_en_basics() -> None:
    """Smoke test that the shipped default greeting set actually matches
    the canonical greetings — protects against accidental regex typo
    that would silently disable the regex branch for every bot owner
    using the defaults."""
    import re

    canonical = ["chào", "Chào", "hi", "hello", "xin chào", "hey",
                 "good morning", "cảm ơn", "thanks", "thank you",
                 "bye", "tạm biệt"]
    for greeting in canonical:
        matched = any(
            re.match(pat, greeting, re.IGNORECASE)
            for pat in DEFAULT_GREETING_PATTERNS
        )
        assert matched, (
            f"DEFAULT_GREETING_PATTERNS should match canonical greeting "
            f"{greeting!r} but none of {list(DEFAULT_GREETING_PATTERNS)!r} did."
        )


# ---------------------------------------------------------------------------
# 9. INTENT_GREETING constant is the literal we emit.
# ---------------------------------------------------------------------------
def test_intent_greeting_constant_value() -> None:
    """The intent label written into state when the gate bypasses must
    be the canonical "greeting" string used by INTENT_CHITCHAT and
    downstream routing — proves zero-hardcode + matches existing taxonomy."""
    from ragbot.shared.constants import INTENT_CHITCHAT

    assert INTENT_GREETING == "greeting"
    assert INTENT_GREETING in INTENT_CHITCHAT
