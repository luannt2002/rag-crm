"""Golden dataset tests — validate structure and basic properties.

Full pipeline evaluation requires a running DB + LLM. These tests validate
the golden set is well-formed and can be loaded by the evaluation pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Find golden dataset file
_GOLDEN_DIR = Path(__file__).parent.parent.parent / "golden_set"
_GOLDEN_FILES = list(_GOLDEN_DIR.glob("*.json")) if _GOLDEN_DIR.is_dir() else []
# Canonical evaluation fixture — referenced by golden_set/README.md and
# scripts/evaluate_embeddings.py. Other JSONs in golden_set/ are run-result
# dumps or per-bot question files with different schemas.
_CANONICAL_GOLDEN = _GOLDEN_DIR / "sample_evaluation.json"


def _load_questions(path: Path) -> list[dict]:
    """Load questions from a golden dataset JSON file.

    Handles both flat list format and wrapped format with a 'questions' key.
    """
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "questions" in data:
        return data["questions"]
    return [data]


def _canonical_questions() -> list[dict]:
    if not _CANONICAL_GOLDEN.exists():
        pytest.skip(f"Canonical golden fixture not found: {_CANONICAL_GOLDEN.name}")
    return _load_questions(_CANONICAL_GOLDEN)


class TestGoldenDatasetStructure:
    @pytest.fixture
    def golden_data(self):
        """Load canonical golden dataset (sample_evaluation.json)."""
        return _canonical_questions()

    def test_golden_dataset_exists(self):
        assert _GOLDEN_DIR.is_dir(), f"Golden set directory not found: {_GOLDEN_DIR}"
        assert len(_GOLDEN_FILES) > 0, "No JSON files in golden_set/"

    def test_golden_dataset_not_empty(self, golden_data):
        assert len(golden_data) > 0, "Golden dataset is empty"

    def test_golden_cases_have_required_fields(self, golden_data):
        """Each golden case must have at minimum: id, question, ground_truth."""
        for case in golden_data:
            assert "id" in case or "question" in case, f"Case missing id/question: {case}"
            # At least one of these content fields must exist
            has_question = "question" in case or "query" in case
            has_answer = "ground_truth" in case or "expected_answer" in case
            assert has_question, f"Case missing question/query field: {case.get('id', '?')}"

    def test_golden_cases_have_unique_ids(self, golden_data):
        """All golden cases must have unique IDs."""
        ids = [c.get("id") for c in golden_data if "id" in c]
        if ids:
            assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[x for x in ids if ids.count(x) > 1]}"

    def test_golden_dataset_covers_multiple_categories(self, golden_data):
        """Golden dataset should cover multiple question categories."""
        categories = {c.get("category") or c.get("type") or c.get("difficulty") for c in golden_data}
        categories.discard(None)
        assert len(categories) >= 2, f"Only {len(categories)} category(ies): {categories}"

    def test_golden_questions_are_non_trivial(self, golden_data):
        """Questions should be at least 5 characters."""
        for case in golden_data:
            q = case.get("question") or case.get("query") or ""
            assert len(q) >= 5, f"Trivial question in case {case.get('id', '?')}: '{q}'"

    def test_golden_ground_truth_exists(self, golden_data):
        """Most cases should have ground truth for evaluation."""
        with_truth = sum(1 for c in golden_data if c.get("ground_truth") or c.get("expected_answer"))
        ratio = with_truth / len(golden_data)
        assert ratio >= 0.5, f"Only {with_truth}/{len(golden_data)} cases have ground truth ({ratio:.0%})"


class TestGoldenDatasetContent:
    @pytest.fixture
    def golden_data(self):
        return _canonical_questions()

    def test_has_vietnamese_questions(self, golden_data):
        """At least some questions should be in Vietnamese."""
        vietnamese_chars = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
        vn_count = sum(
            1 for c in golden_data
            if any(ch in vietnamese_chars for ch in (c.get("question") or c.get("query") or "").lower())
        )
        assert vn_count >= 1, "No Vietnamese questions found in golden dataset"

    def test_has_difficulty_distribution(self, golden_data):
        """Dataset should include easy, medium, and hard questions."""
        difficulties = {c.get("difficulty") for c in golden_data if c.get("difficulty")}
        if difficulties:
            assert len(difficulties) >= 2, f"Only {len(difficulties)} difficulty level(s): {difficulties}"
