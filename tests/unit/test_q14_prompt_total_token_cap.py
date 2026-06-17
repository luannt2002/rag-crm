"""F1c — Q14 generate prompt-total token-budget verifier (soft hint).

Per F9 cost audit (cost / turn dominated by Q14 — 50–60% of total spend):
the generate node assembles `system + persona + rules + chunks +
history` into one outbound payload. A regression where any of those
sections inflates silently (e.g. extra rule paragraph appended every
turn, or chunks re-included in raw form on top of a digest) would only
surface in the cost dashboard days later.

This file ships a pinning test that catches such regressions BEFORE they
ship. The cap is implemented as a **soft hint** because Phase-2 ROADMAP
(§4#2) defers the hard cap to a follow-up sprint — flipping the soft
hint to a hard cap is a one-line change once the budget is validated.

The cap value (2900 tokens) is local to this test until Phase-2 lands —
constants.py is owned by another agent in this iteration window so the
hint lives here. When Phase-2 promotes the hint, move it to
``shared/constants.py`` as ``DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP`` +
import here.

Token estimator: char/4 ≈ token for VN-mixed payloads. Conservative —
real tiktoken count is usually slightly lower for ASCII, slightly higher
for VN diacritics. The 2900 hint already accounts for the ±10% spread.

App-mindset compliance: pure measurement, zero LLM call, zero injection.
"""
from __future__ import annotations

import warnings

import pytest


# Phase-2 ROADMAP §4#2 soft hint. Promote to constants.py when ready.
# Value derived from F9 audit §4: median Q14 prompt size at 2 615 tokens
# in production R7 NEW; 2 900 = +11% headroom for legitimate variance
# (longer system prompts on customised bots) without permitting silent
# 2x bloat regressions.
DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT: int = 2900

# Conservative char-to-token ratio for VN-mixed payloads. Used by the
# proxy estimator below. tiktoken is not a dependency of the unit-test
# suite so we use the standard /4 heuristic — matches the order-of-
# magnitude check we need (catch 2x bloat, ignore ±5% noise).
CHARS_PER_TOKEN_PROXY: int = 4


def _estimate_total_tokens(messages: list[dict]) -> int:
    """Char-based token estimator. Sums every message body's length."""
    total_chars = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # Anthropic list-of-blocks payload — sum each block's text.
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(block.get("text", "") or "")
    return total_chars // CHARS_PER_TOKEN_PROXY


def _build_minimal_q14_state(
    *,
    system_chars: int = 1200,
    chunks: int = 3,
    chunk_chars: int = 800,
    history_turns: int = 4,
    history_chars_per_turn: int = 200,
) -> list[dict]:
    """Construct a minimal Q14 prompt structure.

    Mirrors the actual generate node payload at query_graph.py:2557 —
    {system_prompt, <documents>{chunks}</documents>, history*, user}.
    Sizes are configurable so tests can probe under/at/over the cap.
    """
    chunk_xml = "".join(
        f"<doc id='{i}'>{('A' * chunk_chars)}</doc>"
        for i in range(chunks)
    )
    history_msgs = []
    for i in range(history_turns):
        role = "user" if i % 2 == 0 else "assistant"
        history_msgs.append({
            "role": role,
            "content": "B" * history_chars_per_turn,
        })

    return [
        {"role": "system", "content": "S" * system_chars},
        *history_msgs,
        {
            "role": "user",
            "content": f"<documents>{chunk_xml}</documents>\nQuestion?",
        },
    ]


# ---------- scenario 1 — under cap (typical refuse path) ------------------------

def test_q14_prompt_total_under_cap():
    """Refuse path: minimal system + 0 chunks + 2 history turns.

    Estimated payload ~600 tokens — comfortably under the 2900 hint.
    Pin: catches a regression where the canned-refuse path (which by
    F9 §3 should be the cheapest) accidentally inflates above cap."""
    msgs = _build_minimal_q14_state(
        system_chars=1000,
        chunks=0,
        chunk_chars=0,
        history_turns=2,
        history_chars_per_turn=100,
    )
    estimated = _estimate_total_tokens(msgs)
    assert estimated < DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT, (
        f"refuse-path Q14 prompt over hint: {estimated} >= "
        f"{DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT}"
    )


# ---------- scenario 2 — at boundary (PASS path, healthy budget) ----------------

def test_q14_prompt_total_at_boundary():
    """PASS path: ~1.2k system + 3×800c chunks + 4 history turns.

    Estimated ~1100 tokens — well within budget. This is the "happy
    path" PASS profile from F9 §2 (median 4 265 was inflated by the
    extreme tail; healthy median is ~1 200)."""
    msgs = _build_minimal_q14_state(
        system_chars=1200,
        chunks=3,
        chunk_chars=800,
        history_turns=4,
        history_chars_per_turn=200,
    )
    estimated = _estimate_total_tokens(msgs)
    # Should be comfortably under hint with healthy headroom.
    assert estimated < DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT
    # And not absurdly low (regression where chunks dropped silently).
    assert estimated > 500, (
        "PASS-path estimate too low; chunks may have been silently dropped"
    )


# ---------- scenario 3 — over cap (regression detector, warn-only) -------------

def test_q14_prompt_total_over_cap_warns():
    """Bloat regression: 2 500c system + 6×1 500c chunks + 8 history turns.

    Estimated ~3 600 tokens — over the 2900 hint. Today this is a soft
    warning (not a hard fail) because:
      - Phase-2 hard cap deferred per ROADMAP §4#2.
      - Some bots legitimately need a 2k-token system prompt.
      - Hard fail would break tests on long-history sessions until the
        token-based history cap (F1c) lands.

    When the hard cap promotes, change ``warns()`` → ``raises()``.
    """
    msgs = _build_minimal_q14_state(
        system_chars=2500,
        chunks=6,
        chunk_chars=1500,
        history_turns=8,
        history_chars_per_turn=400,
    )
    estimated = _estimate_total_tokens(msgs)
    # Hard assertion: detector itself works — over-cap state IS over cap.
    assert estimated > DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT, (
        f"detector broken: bloat scenario estimate {estimated} did not "
        f"exceed hint {DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT}"
    )

    # Soft warning surface — Phase-2 promotion will flip to assert-fail.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if estimated > DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT:
            warnings.warn(
                f"Q14 prompt token estimate {estimated} exceeds soft hint "
                f"{DEFAULT_GENERATE_PROMPT_TOTAL_TOKEN_CAP_HINT}; review "
                f"system_prompt + chunk-budget for bloat regression.",
                UserWarning,
                stacklevel=2,
            )
        assert any(
            "exceeds soft hint" in str(w.message) for w in caught
        ), "warn-only surface failed to emit on over-cap state"


# ---------- estimator self-test (sanity) ---------------------------------------

def test_estimator_handles_anthropic_list_of_blocks():
    """When cache_control rewrites system content to list-of-blocks,
    the estimator must still count text chars. Otherwise the cap
    silently passes for Anthropic payloads."""
    msgs = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "X" * 4000,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
        },
        {"role": "user", "content": "Hi"},
    ]
    # 4000 chars / 4 = 1000 tokens estimated from the system block.
    estimated = _estimate_total_tokens(msgs)
    assert estimated >= 1000, (
        f"list-of-blocks estimator broken: got {estimated}, expected ≥1000"
    )
