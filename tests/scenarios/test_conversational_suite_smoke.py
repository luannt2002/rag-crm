"""Smoke test for the M26 conversational eval suites.

Why this exists (M26 — reports/MASTER_PROBLEM_REGISTER_20260622.md line 97):
the factoid eval (entity-name questions -> stats-index fast path) returns a
false COVERAGE 1.00 and hides the real conversational gaps (existence / listing
/ price-of-entity with multiple phrasings + short zones / comparison / fuzzy /
multi-turn / refusal-trap). The conversational suites under this directory
exercise those gaps with ground-truth lifted LITERALLY from the live corpus.

This smoke test is a structural / integrity guard only — it does NOT call the
bot. It asserts every suite parses, matches the agreed schema, that every
non-refusal case carries non-empty literal expected substrings, and that every
refusal-trap case carries none (HALLU=0: a trap must never smuggle a value
the LLM could be graded "correct" for fabricating).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SUITE_DIR = Path(__file__).parent
SUITE_GLOB = "*_conversational_suite.json"

# The three bots the program is graded on (charter + register line 101).
EXPECTED_BOTS = {
    "test-spa-id": "spa",
    "chinh-sach-xe": "xe",
    "thong-tu-09-2020-tt-nhnn": "legal",
}

# Per-case required keys (the agreed M26 case shape).
CASE_KEYS = {"id", "intent_label", "question", "expected_substrings", "must_refuse"}

# Intent families EVERY suite must exercise (the real conversational gaps factoid hid).
REQUIRED_INTENTS = {
    "existence",
    "listing",
    "comparison",
    "fuzzy_synonym",
    "multi_turn",
    "refusal_trap",
}

# Value-lookup family — phrasing/format-sensitive "what is the value of X" intent.
# Retail bots (priced entities) use 'price_of_entity'; the legal bot has no prices,
# its analog is a numeric/level threshold lookup ('threshold'). Every suite must
# carry at least one of these (the multi-phrasing, short-name retrieval gap).
VALUE_LOOKUP_INTENTS = {"price_of_entity", "threshold"}

# Closed set of allowed intent labels (typo guard).
ALLOWED_INTENTS = REQUIRED_INTENTS | VALUE_LOOKUP_INTENTS


def _suite_paths() -> list[Path]:
    return sorted(SUITE_DIR.glob(SUITE_GLOB))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_suite_files_exist_one_per_bot() -> None:
    paths = _suite_paths()
    assert paths, f"no conversational suite found matching {SUITE_GLOB} in {SUITE_DIR}"
    bots = {_load(p)["bot_id"] for p in paths}
    assert bots == set(EXPECTED_BOTS), (
        f"suite bots {sorted(bots)} != expected {sorted(EXPECTED_BOTS)}"
    )


@pytest.mark.parametrize("path", _suite_paths(), ids=lambda p: p.stem)
def test_suite_top_level_schema(path: Path) -> None:
    suite = _load(path)
    for key in ("bot_id", "channel_type", "workspace_id", "note", "questions"):
        assert key in suite, f"{path.name}: missing top-level key {key!r}"
    bot_id = suite["bot_id"]
    assert bot_id in EXPECTED_BOTS, f"{path.name}: unknown bot_id {bot_id!r}"
    assert suite["workspace_id"] == EXPECTED_BOTS[bot_id], (
        f"{path.name}: workspace_id {suite['workspace_id']!r} != "
        f"{EXPECTED_BOTS[bot_id]!r}"
    )
    assert suite["channel_type"] == "web"
    assert isinstance(suite["questions"], list) and suite["questions"], (
        f"{path.name}: questions must be a non-empty list"
    )


@pytest.mark.parametrize("path", _suite_paths(), ids=lambda p: p.stem)
def test_case_schema_and_literal_groundtruth(path: Path) -> None:
    suite = _load(path)
    seen_ids: set[str] = set()
    for case in suite["questions"]:
        cid = case.get("id", "<no-id>")
        assert CASE_KEYS <= set(case), (
            f"{path.name}:{cid}: missing keys {CASE_KEYS - set(case)}"
        )
        assert cid not in seen_ids, f"{path.name}: duplicate case id {cid!r}"
        seen_ids.add(cid)

        assert isinstance(case["question"], str) and case["question"].strip(), (
            f"{path.name}:{cid}: empty question"
        )
        assert isinstance(case["intent_label"], str) and case["intent_label"], (
            f"{path.name}:{cid}: empty intent_label"
        )
        assert isinstance(case["must_refuse"], bool), (
            f"{path.name}:{cid}: must_refuse must be bool"
        )
        subs = case["expected_substrings"]
        assert isinstance(subs, list), (
            f"{path.name}:{cid}: expected_substrings must be a list"
        )

        if case["must_refuse"]:
            # HALLU=0: a refusal trap must carry NO gradeable value.
            assert subs == [], (
                f"{path.name}:{cid}: refusal-trap must have empty "
                f"expected_substrings, got {subs!r}"
            )
            assert case["intent_label"] == "refusal_trap", (
                f"{path.name}:{cid}: must_refuse=True requires intent_label "
                f"'refusal_trap'"
            )
        else:
            # Real-answer case: at least one non-empty literal substring.
            assert subs, f"{path.name}:{cid}: non-refusal case needs >=1 substring"
            for s in subs:
                assert isinstance(s, str) and s.strip(), (
                    f"{path.name}:{cid}: empty substring in {subs!r}"
                )


@pytest.mark.parametrize("path", _suite_paths(), ids=lambda p: p.stem)
def test_suite_covers_required_intents(path: Path) -> None:
    suite = _load(path)
    intents = {c["intent_label"] for c in suite["questions"]}
    unknown = intents - ALLOWED_INTENTS
    assert not unknown, f"{path.name}: unknown intent labels {sorted(unknown)}"
    missing = REQUIRED_INTENTS - intents
    assert not missing, f"{path.name}: missing required intents {sorted(missing)}"
    assert intents & VALUE_LOOKUP_INTENTS, (
        f"{path.name}: must carry >=1 value-lookup case from {sorted(VALUE_LOOKUP_INTENTS)}"
    )


@pytest.mark.parametrize("path", _suite_paths(), ids=lambda p: p.stem)
def test_multi_turn_cases_carry_history(path: Path) -> None:
    """multi_turn cases must declare a >=2-turn prior context (the follow-up gap)."""
    suite = _load(path)
    for case in suite["questions"]:
        if case["intent_label"] != "multi_turn":
            continue
        cid = case["id"]
        history = case.get("history")
        assert isinstance(history, list) and len(history) >= 1, (
            f"{path.name}:{cid}: multi_turn case must carry a non-empty 'history' "
            "list of prior user turns (coreference / follow-up)"
        )
        for turn in history:
            assert isinstance(turn, str) and turn.strip(), (
                f"{path.name}:{cid}: empty history turn"
            )


@pytest.mark.parametrize("path", _suite_paths(), ids=lambda p: p.stem)
def test_price_substrings_are_bare_digit_groups(path: Path) -> None:
    """Price-of-entity ground-truth must be format-agnostic digit groups.

    The factoid set hid a real retrieval bug (xe: user types 205/55R16, priced
    row uses 205/55/16). For price cases we store the bare digit run (e.g.
    '1044000') so a faithful answer matches regardless of 1.044.000 / 1,044,000
    formatting. This guards that we did not accidentally encode a formatted
    literal that a faithful answer would never substring-match.
    """
    suite = _load(path)
    for case in suite["questions"]:
        if case["intent_label"] != "price_of_entity":
            continue
        cid = case["id"]
        has_price_token = any(re.fullmatch(r"\d{4,}", s) for s in case["expected_substrings"])
        assert has_price_token, (
            f"{path.name}:{cid}: price_of_entity needs >=1 bare-digit price token "
            f"(>=4 digits), got {case['expected_substrings']!r}"
        )


@pytest.mark.parametrize("path", _suite_paths(), ids=lambda p: p.stem)
def test_price_lookup_uses_multiple_phrasings(path: Path) -> None:
    """M26 mandate: price-of-entity asked with BOTH leading ('Giá X bao nhiêu')
    and trailing ('X ... bao nhiêu tiền') phrasing — the factoid set only ever
    asked the entity name, hiding the phrasing-sensitivity of retrieval. Only
    the retail bots (priced entities) carry price_of_entity cases.
    """
    suite = _load(path)
    questions = [
        c["question"] for c in suite["questions"]
        if c["intent_label"] == "price_of_entity"
    ]
    if not questions:
        return
    leading = any(q.strip().lower().startswith("giá") for q in questions)
    trailing = any(re.search(r"bao nhiêu", q, re.IGNORECASE) for q in questions)
    assert leading and trailing, (
        f"{path.name}: price_of_entity needs both a leading 'Giá ...' phrasing and "
        f"a trailing '... bao nhiêu' phrasing (leading={leading}, trailing={trailing})"
    )
