"""Verify CRAG 3-state grading: prompt vocab yes/no/partial maps to internal relevant/irrelevant/ambiguous."""

from __future__ import annotations


def test_crag_three_states_in_prompt():
    """Three grade verbs (yes/no/partial) appear in CRAG grader prompt for every language pack."""
    from ragbot.shared.i18n import PACKS

    for lang, pack in PACKS.items():
        lower = pack.prompt_grader.lower()
        assert "yes" in lower, f"{lang}: 'yes' missing"
        assert "no" in lower, f"{lang}: 'no' missing"
        assert "partial" in lower, f"{lang}: 'partial' missing"


def test_crag_grade_constants_exist():
    """Internal CRAG grade constants resolve to relevant/irrelevant/ambiguous strings."""
    from ragbot.orchestration.query_graph import (
        CRAG_GRADE_AMBIGUOUS,
        CRAG_GRADE_IRRELEVANT,
        CRAG_GRADE_RELEVANT,
    )

    assert CRAG_GRADE_RELEVANT == "relevant"
    assert CRAG_GRADE_IRRELEVANT == "irrelevant"
    assert CRAG_GRADE_AMBIGUOUS == "ambiguous"


def test_crag_valid_grades_frozenset():
    """_CRAG_VALID_GRADES contains exactly the three internal states."""
    from ragbot.orchestration.query_graph import _CRAG_VALID_GRADES

    assert "relevant" in _CRAG_VALID_GRADES
    assert "irrelevant" in _CRAG_VALID_GRADES
    assert "ambiguous" in _CRAG_VALID_GRADES
    assert len(_CRAG_VALID_GRADES) == 3


class TestCRAGGradingBehavior:
    def test_three_grade_verbs_in_grade_prompt(self):
        """Schema-aligned grade verbs (yes/no/partial) present in grader prompt all languages."""
        from ragbot.shared.i18n import PACKS

        for lang, pack in PACKS.items():
            assert "yes" in pack.prompt_grader.lower(), f"{lang}: missing 'yes'"
            assert "no" in pack.prompt_grader.lower(), f"{lang}: missing 'no'"
            assert "partial" in pack.prompt_grader.lower(), f"{lang}: missing 'partial'"

    def test_schema_to_internal_mapping(self):
        """Schema vocab yes/no/partial maps to internal relevant/irrelevant/ambiguous."""
        from ragbot.orchestration.query_graph import (
            CRAG_GRADE_AMBIGUOUS,
            CRAG_GRADE_IRRELEVANT,
            CRAG_GRADE_RELEVANT,
        )

        mapping = {
            "yes": CRAG_GRADE_RELEVANT,
            "no": CRAG_GRADE_IRRELEVANT,
            "partial": CRAG_GRADE_AMBIGUOUS,
        }
        assert mapping["yes"] == "relevant"
        assert mapping["no"] == "irrelevant"
        assert mapping["partial"] == "ambiguous"

    def test_all_relevant_sets_retrieval_adequate_true(self):
        """When every chunk is graded relevant, retrieval_adequate evaluates True."""
        from ragbot.orchestration.query_graph import (
            CRAG_GRADE_IRRELEVANT,
            CRAG_GRADE_RELEVANT,
        )

        graded_chunks = [
            {"content": "chunk A", "relevance": CRAG_GRADE_RELEVANT},
            {"content": "chunk B", "relevance": CRAG_GRADE_RELEVANT},
        ]
        has_relevant = any(c["relevance"] == CRAG_GRADE_RELEVANT for c in graded_chunks)
        all_irrelevant = all(c["relevance"] == CRAG_GRADE_IRRELEVANT for c in graded_chunks)

        assert has_relevant is True
        assert all_irrelevant is False

    def test_all_irrelevant_sets_retrieval_adequate_false(self):
        """When every chunk is irrelevant, retrieval_adequate evaluates False."""
        from ragbot.orchestration.query_graph import CRAG_GRADE_IRRELEVANT

        graded_chunks = [
            {"content": "chunk A", "relevance": CRAG_GRADE_IRRELEVANT},
            {"content": "chunk B", "relevance": CRAG_GRADE_IRRELEVANT},
        ]
        has_relevant = any(c["relevance"] != CRAG_GRADE_IRRELEVANT for c in graded_chunks)
        all_irrelevant = all(c["relevance"] == CRAG_GRADE_IRRELEVANT for c in graded_chunks)

        assert has_relevant is False
        assert all_irrelevant is True

    def test_mixed_ambiguous_triggers_rewrite(self):
        """All-ambiguous (no relevant) input triggers rewrite path."""
        from ragbot.orchestration.query_graph import (
            CRAG_GRADE_AMBIGUOUS,
            CRAG_GRADE_RELEVANT,
        )

        graded_chunks = [
            {"content": "chunk A", "relevance": CRAG_GRADE_AMBIGUOUS},
            {"content": "chunk B", "relevance": CRAG_GRADE_AMBIGUOUS},
        ]
        has_relevant = any(c["relevance"] == CRAG_GRADE_RELEVANT for c in graded_chunks)
        assert has_relevant is False

    def test_relevant_plus_ambiguous_keeps_both(self):
        """Relevant + ambiguous mix retains both chunks for generation."""
        from ragbot.orchestration.query_graph import (
            CRAG_GRADE_AMBIGUOUS,
            CRAG_GRADE_RELEVANT,
        )

        graded_chunks = [
            {"content": "chunk A", "relevance": CRAG_GRADE_RELEVANT},
            {"content": "chunk B", "relevance": CRAG_GRADE_AMBIGUOUS},
        ]
        has_relevant = any(c["relevance"] == CRAG_GRADE_RELEVANT for c in graded_chunks)
        kept = [c for c in graded_chunks if c["relevance"] in (CRAG_GRADE_RELEVANT, CRAG_GRADE_AMBIGUOUS)]

        assert has_relevant is True
        assert len(kept) == 2
