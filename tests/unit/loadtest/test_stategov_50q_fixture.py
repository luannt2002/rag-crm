"""Pin tests for stategov 50Q load test fixture.

Validates:
- JSON loads OK
- Total questions == 50
- Pattern distribution exactly: 15+10+8+5+5+3+4 = 50
- Trap HALLU questions have ``sacred_trap: True`` flag
- All non-trap questions carry non-empty ``expected_keywords``
- All question IDs are unique
- Each question carries required schema fields

The fixture is read-only data consumed by the G4 load test runner
(``scripts/loadtest_stategov_50q.py``). These tests guard against accidental
schema drift while editing the question bank.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "loadtest"
    / "stategov_questions_50q.json"
)

EXPECTED_PATTERN_COUNTS: dict[str, int] = {
    "single_entity": 15,
    "multi_entity": 10,
    "typo_no_diacritic": 8,
    "abbreviation": 5,
    "semantic": 5,
    "cross_reference": 3,
    "trap_hallu": 4,
}
EXPECTED_TOTAL: int = sum(EXPECTED_PATTERN_COUNTS.values())  # 50

REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "category",
    "question",
    "expected_keywords",
    "expected_refuse",
    "expected_citation_chunk_hint",
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    assert FIXTURE_PATH.exists(), f"fixture missing: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_loads_as_valid_json(fixture: dict) -> None:
    assert isinstance(fixture, dict)
    assert fixture.get("version") == 1
    assert fixture.get("bot_id") == "stategov-banking"
    assert fixture.get("channel_type") == "web"
    assert isinstance(fixture.get("questions"), list)


def test_total_question_count_is_50(fixture: dict) -> None:
    assert len(fixture["questions"]) == EXPECTED_TOTAL


def test_pattern_distribution_matches_spec(fixture: dict) -> None:
    counts = Counter(q["category"] for q in fixture["questions"])
    assert dict(counts) == EXPECTED_PATTERN_COUNTS, (
        f"pattern counts drifted: got {dict(counts)}, want {EXPECTED_PATTERN_COUNTS}"
    )


def test_trap_hallu_questions_marked_sacred(fixture: dict) -> None:
    traps = [q for q in fixture["questions"] if q["category"] == "trap_hallu"]
    assert len(traps) == EXPECTED_PATTERN_COUNTS["trap_hallu"]
    for t in traps:
        assert t.get("sacred_trap") is True, f"trap missing sacred flag: {t['id']}"
        assert t.get("expected_refuse") is True, (
            f"trap must expect refuse: {t['id']}"
        )
        assert t.get("trap_reason"), f"trap missing trap_reason: {t['id']}"


def test_non_trap_questions_have_non_empty_keywords(fixture: dict) -> None:
    for q in fixture["questions"]:
        if q["category"] == "trap_hallu":
            continue
        kws = q.get("expected_keywords") or []
        assert kws, f"non-trap question {q['id']} has empty expected_keywords"
        assert q.get("expected_refuse") is False, (
            f"non-trap question {q['id']} must expect answer (refuse=False)"
        )


def test_all_question_ids_unique(fixture: dict) -> None:
    ids = [q["id"] for q in fixture["questions"]]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate question ids: {dupes}"


def test_every_question_has_required_fields(fixture: dict) -> None:
    for q in fixture["questions"]:
        missing = [f for f in REQUIRED_FIELDS if f not in q]
        assert not missing, f"question {q.get('id', '?')} missing fields: {missing}"


def test_aggregate_targets_present(fixture: dict) -> None:
    assert fixture.get("expected_pass_rate_target") == 0.80
    assert fixture.get("expected_hallu_max") == 0
    assert fixture.get("expected_refuse_count_trap") == 4
