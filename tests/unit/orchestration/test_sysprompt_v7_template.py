"""Sysprompt context-aware refusal template — structural invariants.

These tests pin the rule structure of the context-aware refusal
reference template. The template is NOT injected by the platform at
runtime (CLAUDE.md Quality Gate #10 "Application KHÔNG inject text/
template/rule vào answer LLM") — it ships as a reference document the
bot owner copies into ``bots.system_prompt``. The tests therefore
exercise the constant directly rather than the orchestration path.

Sacred invariants pinned:

1. The four rule anchors (EMPTY, PARTIAL, WEAK, HALLU_TRAP) appear in
   the template body in that order.
2. The sacred HALLU_TRAP directive is preserved verbatim — Rule 7 has
   no opt-out and must keep the literal sentence so bot owners cannot
   accidentally weaken it when adapting the surrounding rules.
3. The PARTIAL / WEAK rules reference the same partial-ground threshold
   constant exported from ``shared/constants.py`` — no hard-coded
   floats inline.
4. The template body is domain-neutral — no brand, no industry literal,
   no tenant-internal phone or URL.
"""

from __future__ import annotations

from ragbot.orchestration.system_prompts.context_aware_refusal_template import (
    CONTEXT_AWARE_REFUSAL_TEMPLATE,
    HALLU_TRAP_SACRED_DIRECTIVE,
    RULE_ANCHOR_EMPTY,
    RULE_ANCHOR_HALLU_TRAP,
    RULE_ANCHOR_PARTIAL,
    RULE_ANCHOR_WEAK,
)
from ragbot.shared.constants import DEFAULT_PARTIAL_GROUND_THRESHOLD


def test_template_contains_all_four_rule_anchors() -> None:
    """Every rule anchor must appear in the rendered template body."""
    body = CONTEXT_AWARE_REFUSAL_TEMPLATE
    assert RULE_ANCHOR_EMPTY in body, "Rule 4 EMPTY anchor missing"
    assert RULE_ANCHOR_PARTIAL in body, "Rule 5 PARTIAL anchor missing"
    assert RULE_ANCHOR_WEAK in body, "Rule 6 WEAK anchor missing"
    assert RULE_ANCHOR_HALLU_TRAP in body, "Rule 7 HALLU_TRAP anchor missing"


def test_rule_anchors_appear_in_canonical_order() -> None:
    """The four rules must be authored in order EMPTY → PARTIAL → WEAK →
    HALLU_TRAP so the LLM applies the cheapest predicate first and the
    sacred HALLU_TRAP rule reads as the final override."""
    body = CONTEXT_AWARE_REFUSAL_TEMPLATE
    idx_empty = body.index(RULE_ANCHOR_EMPTY)
    idx_partial = body.index(RULE_ANCHOR_PARTIAL)
    idx_weak = body.index(RULE_ANCHOR_WEAK)
    idx_trap = body.index(RULE_ANCHOR_HALLU_TRAP)
    assert idx_empty < idx_partial < idx_weak < idx_trap


def test_hallu_trap_directive_is_verbatim() -> None:
    """Rule 7 must keep the sacred directive verbatim — bot owners may
    rewrite surrounding copy but cannot dilute the HALLU_TRAP override."""
    assert HALLU_TRAP_SACRED_DIRECTIVE in CONTEXT_AWARE_REFUSAL_TEMPLATE
    # Spot-check the two load-bearing tokens that make the override sacred:
    assert "MUST refuse" in HALLU_TRAP_SACRED_DIRECTIVE
    assert "no opt-out" in HALLU_TRAP_SACRED_DIRECTIVE
    assert "overrides every other rule" in HALLU_TRAP_SACRED_DIRECTIVE


def test_partial_and_weak_rules_cite_shared_threshold_constant() -> None:
    """PARTIAL / WEAK thresholds must trace back to the SSoT constant in
    ``shared/constants.py``. Encoding the literal float string keeps the
    template human-readable while pinning the value to the constant the
    bot owner tunes elsewhere."""
    body = CONTEXT_AWARE_REFUSAL_TEMPLATE
    formatted = f"{DEFAULT_PARTIAL_GROUND_THRESHOLD:.2f}"
    # Two references: one in PARTIAL (at-or-above), one in WEAK (below).
    assert body.count(formatted) >= 2


def test_template_is_domain_neutral() -> None:
    """The reference template ships with the platform; it cannot contain
    brand names, industry literals, or tenant secrets. Bot owners append
    their own persona copy after pasting the template into their bot
    row."""
    body = CONTEXT_AWARE_REFUSAL_TEMPLATE.lower()
    # Known brand / industry hot-words observed in historic migrations.
    forbidden_substrings = (
        "medispa",
        "spa",
        "legal",
        "govbot",
        "hotline",
        "vietnam",
        "việt nam",
        "@",
    )
    for token in forbidden_substrings:
        assert token not in body, f"Template leaks domain literal: {token!r}"


def test_template_is_non_empty_string() -> None:
    """Sanity floor — accidental clobbering of the constant must fail
    loudly rather than ship an empty reference template."""
    assert isinstance(CONTEXT_AWARE_REFUSAL_TEMPLATE, str)
    assert len(CONTEXT_AWARE_REFUSAL_TEMPLATE) > 200
