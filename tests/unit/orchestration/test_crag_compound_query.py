"""Regression test for mega-sprint G10b — CRAG over-strict on compound queries.

Issue 10 (live evidence Case B "X and Y in document Z"):
- Decompose splits compound query into 2 sub-queries -> 2 retrieve branches.
- RRF merges chunks -> MMR/rerank may collapse to 1 surviving chunk.
- The grader sees the FULL compound query and the surviving chunk that
  only addresses ONE sub-entity — rationally grades it "no" (does NOT
  fully answer the FULL question).
- Pre-fix: ``crag_grade_distribution relevant=0 irrelevant=1 total=1`` ->
  retrieval_adequate=False -> empty answer. Worse UX than the half-correct
  pre-Issue-1 behaviour (which at least cited some chunks).

Fix: intent-aware lenient grading for synthesis-style intents
(comparison, multi_hop, aggregation). When the intent is compound, an
"irrelevant" verdict is re-mapped to "ambiguous" so the chunk is kept
in the graded pool and downstream ``generate`` can synthesize. HALLU=0
sacred is preserved by the existing downstream ``grounding_check``
guardrail (which evaluates the FINAL answer against the chunks).

Domain-neutral: tests use generic intent labels and grade verbs only.
No legal / medical / brand vocabulary appears in fixtures.
"""

from __future__ import annotations

import pytest

from ragbot.orchestration.query_graph import (
    CRAG_GRADE_AMBIGUOUS,
    CRAG_GRADE_IRRELEVANT,
    CRAG_GRADE_RELEVANT,
    _remap_grade_for_intent,
)
from ragbot.shared.constants import (
    DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
    DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
    INTENT_AGGREGATION,
    INTENT_COMPARISON,
    INTENT_FACTOID,
    INTENT_GREETING,
    INTENT_MULTI_HOP,
)


# --------------------------------------------------------------------------- #
# Constants surface — defaults exposed in shared/constants.py                 #
# --------------------------------------------------------------------------- #


def test_lenient_grade_intents_includes_synthesis_and_comparison() -> None:
    """The default lenient set covers every compound-style intent."""
    assert INTENT_COMPARISON in DEFAULT_CRAG_LENIENT_GRADE_INTENTS
    assert INTENT_MULTI_HOP in DEFAULT_CRAG_LENIENT_GRADE_INTENTS
    assert INTENT_AGGREGATION in DEFAULT_CRAG_LENIENT_GRADE_INTENTS


def test_lenient_grade_intents_excludes_factoid_and_chitchat() -> None:
    """Factoid and chitchat-style intents must stay STRICT."""
    assert INTENT_FACTOID not in DEFAULT_CRAG_LENIENT_GRADE_INTENTS
    assert INTENT_GREETING not in DEFAULT_CRAG_LENIENT_GRADE_INTENTS


def test_lenient_grade_default_enabled_is_true() -> None:
    """Compound-intent leniency ships ON by default — Issue 10 fix."""
    assert DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED is True


# --------------------------------------------------------------------------- #
# Helper contract — _remap_grade_for_intent                                   #
# --------------------------------------------------------------------------- #


def test_factoid_irrelevant_stays_irrelevant() -> None:
    """Strict intents (factoid) MUST NOT promote 'no' -> 'partial'."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_IRRELEVANT,
        intent=INTENT_FACTOID,
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=True,
    )
    assert out == CRAG_GRADE_IRRELEVANT


@pytest.mark.parametrize(
    "intent",
    [INTENT_COMPARISON, INTENT_MULTI_HOP, INTENT_AGGREGATION],
)
def test_compound_intent_irrelevant_promoted_to_ambiguous(intent: str) -> None:
    """Compound intents promote 'irrelevant' -> 'ambiguous' so chunk is kept."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_IRRELEVANT,
        intent=intent,
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=True,
    )
    assert out == CRAG_GRADE_AMBIGUOUS


def test_compound_intent_relevant_passes_through_unchanged() -> None:
    """Verdict 'yes' is never demoted regardless of intent."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_RELEVANT,
        intent=INTENT_COMPARISON,
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=True,
    )
    assert out == CRAG_GRADE_RELEVANT


def test_compound_intent_ambiguous_passes_through_unchanged() -> None:
    """Verdict 'partial' (already ambiguous) stays ambiguous."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_AMBIGUOUS,
        intent=INTENT_MULTI_HOP,
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=True,
    )
    assert out == CRAG_GRADE_AMBIGUOUS


def test_lenient_disabled_keeps_irrelevant_for_compound_intent() -> None:
    """Operator can disable leniency via system_config — escape hatch."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_IRRELEVANT,
        intent=INTENT_COMPARISON,
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=False,
    )
    assert out == CRAG_GRADE_IRRELEVANT


def test_unknown_intent_falls_back_to_strict() -> None:
    """Intent not in lenient set behaves strict (preserves verdict)."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_IRRELEVANT,
        intent="some_future_intent",
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=True,
    )
    assert out == CRAG_GRADE_IRRELEVANT


def test_empty_intent_string_falls_back_to_strict() -> None:
    """Missing intent (empty string) MUST default to strict — fail-safe."""
    out = _remap_grade_for_intent(
        CRAG_GRADE_IRRELEVANT,
        intent="",
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=True,
    )
    assert out == CRAG_GRADE_IRRELEVANT


# --------------------------------------------------------------------------- #
# Behaviour — Case B regression (compound query with single surviving chunk)  #
# --------------------------------------------------------------------------- #


def test_case_b_compound_intent_single_chunk_kept_post_fix() -> None:
    """Live Case B: 8 chunks RRF-merged -> 1 surviving after MMR. LLM grades
    the chunk 'no' because it only covers ONE of the two compound entities.

    Pre-fix: counts={relevant:0, irrelevant:1, ambiguous:0} ->
    retrieval_adequate=False -> chunks_used=0 -> empty answer.

    Post-fix: counts={relevant:0, irrelevant:0, ambiguous:1} ->
    chunk kept, retrieval_adequate=True (graded length > 0).
    """
    grade_counts = {
        CRAG_GRADE_RELEVANT: 0,
        CRAG_GRADE_IRRELEVANT: 0,
        CRAG_GRADE_AMBIGUOUS: 0,
    }
    raw_verdict = CRAG_GRADE_IRRELEVANT  # what LLM returned for the chunk
    intent = INTENT_COMPARISON
    remapped = _remap_grade_for_intent(
        raw_verdict,
        intent=intent,
        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
        lenient_enabled=DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
    )
    grade_counts[remapped] += 1

    # Simulates the post-CRAG keep-condition (chunk is kept iff ambiguous or relevant).
    kept_after_remap = grade_counts[CRAG_GRADE_AMBIGUOUS] + grade_counts[CRAG_GRADE_RELEVANT]
    assert kept_after_remap == 1, (
        "compound intent must keep the surviving chunk so generate can synthesize a partial answer"
    )
    assert grade_counts[CRAG_GRADE_IRRELEVANT] == 0
