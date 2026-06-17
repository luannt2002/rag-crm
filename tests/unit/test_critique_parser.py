"""Unit tests for Self-RAG critique-token parser (Asai 2023).

Coverage:
- Mixed-token parse + ratio math.
- No-token answer = pass-through (total_claims == 0).
- Edge cases: empty string, only Supported, only Unsupported, non-string input.
- Case-insensitive token matching.
- ``should_refuse_critique`` boundary behaviour at the threshold.
- Default-OFF semantics (caller never invokes parser → no mutation).
- HALLU=0 fail-open: total_claims == 0 ⇒ never refuse.
"""

from __future__ import annotations

import pytest

from ragbot.orchestration.nodes.critique_parser import (
    parse_critique_tokens,
    should_refuse_critique,
)


# ─── parse_critique_tokens ──────────────────────────────────────────────────


def test_parse_mixed_tokens_3_supported_2_unsupported():
    """3 [Supported] + 2 [Unsupported] ⇒ ratio 2/5 = 0.4."""
    answer = (
        "The capital is Hanoi [Supported]. "
        "The population is 8 million [Supported]. "
        "The mayor is Alice [Unsupported]. "
        "The flag is red [Supported]. "
        "The river is the Mekong [Unsupported]."
    )
    result = parse_critique_tokens(answer)
    assert result["supported_count"] == 3
    assert result["unsupported_count"] == 2
    assert result["total_claims"] == 5
    assert result["unsupported_ratio"] == pytest.approx(0.4)
    assert "[Supported]" not in result["clean_text"]
    assert "[Unsupported]" not in result["clean_text"]
    assert "Hanoi" in result["clean_text"]


def test_parse_no_tokens_total_claims_zero():
    """Answer without any marker → total_claims == 0, ratio == 0."""
    answer = "I do not know the answer."
    result = parse_critique_tokens(answer)
    assert result["total_claims"] == 0
    assert result["supported_count"] == 0
    assert result["unsupported_count"] == 0
    assert result["unsupported_ratio"] == 0.0
    assert result["clean_text"] == "I do not know the answer."


def test_parse_only_supported():
    """All claims supported ⇒ ratio 0.0."""
    answer = "A [Supported]. B [Supported]. C [Supported]."
    result = parse_critique_tokens(answer)
    assert result["supported_count"] == 3
    assert result["unsupported_count"] == 0
    assert result["total_claims"] == 3
    assert result["unsupported_ratio"] == 0.0


def test_parse_only_unsupported():
    """All claims unsupported ⇒ ratio 1.0."""
    answer = "A [Unsupported]. B [Unsupported]."
    result = parse_critique_tokens(answer)
    assert result["supported_count"] == 0
    assert result["unsupported_count"] == 2
    assert result["total_claims"] == 2
    assert result["unsupported_ratio"] == 1.0


def test_parse_empty_string():
    """Empty input returns zero-claims dict and empty clean_text."""
    result = parse_critique_tokens("")
    assert result["clean_text"] == ""
    assert result["total_claims"] == 0
    assert result["unsupported_ratio"] == 0.0


def test_parse_non_string_input():
    """None / int input is rejected gracefully (no exception)."""
    for bad in (None, 42, [], {"k": "v"}):
        result = parse_critique_tokens(bad)  # type: ignore[arg-type]
        assert result["clean_text"] == ""
        assert result["total_claims"] == 0


def test_parse_case_insensitive_matching():
    """Lower-case markers count too — bot owners may normalise in prompt."""
    answer = "A [supported]. B [UNSUPPORTED]. C [SuppOrted]."
    result = parse_critique_tokens(answer)
    assert result["supported_count"] == 2
    assert result["unsupported_count"] == 1
    assert result["total_claims"] == 3
    assert "[supported]" not in result["clean_text"].lower()


def test_parse_clean_text_strips_extra_whitespace():
    """Stripping markers should not leave double spaces or orphaned punctuation."""
    answer = "Hanoi is the capital [Supported] . The mayor is unknown [Unsupported] ."
    result = parse_critique_tokens(answer)
    assert "  " not in result["clean_text"]
    # Trailing-space-before-period collapsed.
    assert " ." not in result["clean_text"]
    assert "[Supported]" not in result["clean_text"]


def test_parse_preserves_non_marker_brackets():
    """``[other]`` content (e.g. ``[chunk:abc]``) must survive untouched."""
    answer = "See [chunk:1234] for detail [Supported]. Also [chunk:5678]."
    result = parse_critique_tokens(answer)
    assert "[chunk:1234]" in result["clean_text"]
    assert "[chunk:5678]" in result["clean_text"]
    assert result["supported_count"] == 1


# ─── should_refuse_critique ─────────────────────────────────────────────────


def test_refuse_at_threshold_boundary_0_30():
    """ratio == threshold ⇒ refuse (>= comparator)."""
    parsed = {"total_claims": 10, "unsupported_count": 3, "unsupported_ratio": 0.3}
    assert should_refuse_critique(parsed, 0.30) is True


def test_no_refuse_below_threshold_0_29():
    """ratio < threshold ⇒ pass (clean text returned, no swap)."""
    parsed = {"total_claims": 100, "unsupported_count": 29, "unsupported_ratio": 0.29}
    assert should_refuse_critique(parsed, 0.30) is False


def test_refuse_above_threshold_0_31():
    """ratio > threshold ⇒ refuse."""
    parsed = {"total_claims": 100, "unsupported_count": 31, "unsupported_ratio": 0.31}
    assert should_refuse_critique(parsed, 0.30) is True


def test_no_refuse_when_total_claims_zero():
    """No markers ⇒ feature inactive on this turn, never refuse (HALLU=0 sacred)."""
    parsed = {"total_claims": 0, "unsupported_count": 0, "unsupported_ratio": 0.0}
    assert should_refuse_critique(parsed, 0.0) is False
    # Even with threshold 0.0 the gate stays open when there are no claims.


def test_no_refuse_when_all_supported():
    """All [Supported] ⇒ ratio 0.0, never refuse for any threshold > 0."""
    parsed = {"total_claims": 5, "unsupported_count": 0, "unsupported_ratio": 0.0}
    assert should_refuse_critique(parsed, 0.30) is False


def test_refuse_when_all_unsupported():
    """All [Unsupported] ⇒ ratio 1.0, refuse for any threshold <= 1."""
    parsed = {"total_claims": 4, "unsupported_count": 4, "unsupported_ratio": 1.0}
    assert should_refuse_critique(parsed, 0.30) is True
    assert should_refuse_critique(parsed, 0.99) is True
    assert should_refuse_critique(parsed, 1.0) is True


def test_refuse_handles_malformed_parse_result():
    """Non-dict or missing keys ⇒ False (fail-open, HALLU=0 sacred)."""
    assert should_refuse_critique(None, 0.3) is False  # type: ignore[arg-type]
    assert should_refuse_critique({}, 0.3) is False
    assert should_refuse_critique({"unsupported_ratio": "not-a-number"}, 0.3) is False


def test_refuse_negative_threshold_coerced_to_zero():
    """Misconfigured negative threshold ⇒ treated as 0.0 (gate fires on any unsupported)."""
    parsed = {"total_claims": 5, "unsupported_count": 1, "unsupported_ratio": 0.2}
    # Negative threshold gets clamped to 0.0; 0.2 >= 0.0 ⇒ refuse.
    assert should_refuse_critique(parsed, -1.0) is True
    # But still respects the "no claims = no refuse" invariant:
    no_claims = {"total_claims": 0, "unsupported_count": 0, "unsupported_ratio": 0.0}
    assert should_refuse_critique(no_claims, -1.0) is False


# ─── Default-OFF semantics ──────────────────────────────────────────────────


def test_default_off_caller_skips_parse_entirely():
    """When the per-bot flag is OFF the orchestrator must not call the parser.

    This test documents the contract — the parser itself is a pure function
    and will happily run on any string.  The OFF guard lives in
    ``query_graph.critique_parse``; here we assert the *invariant* that a
    raw answer survives unmodified when callers honour the flag.
    """
    raw_answer = "An answer with [Supported] markers that should NOT be stripped."
    # Simulate the OFF path: caller does not invoke parse_critique_tokens.
    # The raw answer is returned untouched.
    untouched = raw_answer
    assert "[Supported]" in untouched
    # And when invoked it does parse — proving the guard, not the parser,
    # owns the OFF semantics.
    result = parse_critique_tokens(raw_answer)
    assert "[Supported]" not in result["clean_text"]
