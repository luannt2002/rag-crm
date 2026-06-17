"""V4 invariant guard: intent strings must live only in DTO + constants.

If any orchestration / worker / infrastructure file inlines a tuple or
dict-keyed off a hardcoded intent label, this test fails. Whitelist
covers the 4 files that are *authoritative* (DTO Literal, constants
registry, prompt examples, vocab map).

Closes user mandate (luannt-question-v2-prompt.md): adding an intent
must NOT require a source-edit in orchestration.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ragbot.shared.constants import (
    DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT,
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_SKIP_REFLECT_INTENTS,
    DEFAULT_SKIP_REWRITE_INTENTS,
    INTENT_CHITCHAT,
    INTENT_RETRIEVAL_BEARING,
    INTENT_SYNTHESIS,
)

REAL_INTENTS = frozenset(
    {
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
        "chitchat",
        "vu_vo",
    }
)

DEAD_TEST_LABELS = frozenset(
    {"hallucination_trap", "off_topic", "ambiguous", "discovery"}
)

WHITELISTED_FILES = frozenset(
    {
        "src/ragbot/application/dto/llm_schemas.py",  # Pydantic Literal source of truth
        "src/ragbot/shared/constants.py",  # registry: frozensets + token map
        "src/ragbot/shared/types.py",  # legacy QueryIntent (slated for delete)
        "src/ragbot/shared/i18n.py",  # prompt examples — values mirror DTO
        "src/ragbot/application/services/vocabulary_expander.py",  # EN→VI map keys collide w/ intent labels
    }
)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("project root not found")


def test_no_dead_test_labels_in_token_map() -> None:
    """Dead test-category labels (golden_set.json) must not pollute the
    production intent token map. Re-introducing one is a regression."""
    leak = set(DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT) & DEAD_TEST_LABELS
    assert not leak, f"dead test-label keys in intent map: {leak}"


def test_intent_groups_are_subsets_of_real_intents() -> None:
    """All advertised group frozensets/tuples must align with the
    classifier output Literal. Catches drift if someone adds a string
    here without wiring DTO."""
    for label, group in (
        ("INTENT_CHITCHAT", INTENT_CHITCHAT),
        ("INTENT_SYNTHESIS", INTENT_SYNTHESIS),
        ("INTENT_RETRIEVAL_BEARING", INTENT_RETRIEVAL_BEARING),
        ("DEFAULT_SKIP_REWRITE_INTENTS", set(DEFAULT_SKIP_REWRITE_INTENTS)),
        ("DEFAULT_SKIP_REFLECT_INTENTS", set(DEFAULT_SKIP_REFLECT_INTENTS)),
    ):
        leak = set(group) - REAL_INTENTS
        assert not leak, f"{label} contains non-classifier intents: {leak}"


def test_intent_fallback_is_real_classifier_output() -> None:
    assert DEFAULT_INTENT_FALLBACK in REAL_INTENTS


@pytest.mark.parametrize(
    "intent",
    sorted(REAL_INTENTS),
)
def test_every_real_intent_has_token_budget_or_default(intent: str) -> None:
    """Either explicit cap OR the ``default`` fallback must cover every
    real intent."""
    has_cap = intent in DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT
    has_default = "default" in DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT
    assert has_cap or has_default, (
        f"intent={intent} has no explicit cap and no default fallback"
    )


def test_no_inline_intent_literal_in_hot_path() -> None:
    """Source scan: orchestration / workers / infrastructure must not
    inline-tuple-compare on intent strings outside the whitelist."""
    root = _project_root()
    inline_pattern = re.compile(
        r'"(greeting|chitchat|vu_vo|feedback|factoid|comparison|aggregation|multi_hop|out_of_scope)"'
    )
    hot_path_globs = (
        "src/ragbot/orchestration/**/*.py",
        "src/ragbot/interfaces/workers/**/*.py",
        "src/ragbot/infrastructure/**/*.py",
        "src/ragbot/application/services/**/*.py",
    )
    violations: list[str] = []
    for pattern in hot_path_globs:
        for path in root.glob(pattern):
            rel = str(path.relative_to(root))
            if rel in WHITELISTED_FILES:
                continue
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            in_docstring = False
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.split("#", 1)[0]
                triple_count = stripped.count('"""') + stripped.count("'''")
                if triple_count % 2 == 1:
                    in_docstring = not in_docstring
                    continue
                if in_docstring:
                    continue
                if inline_pattern.search(stripped):
                    violations.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not violations, (
        "Inline intent literal leaked into hot-path code (use constants.INTENT_*"
        " or DEFAULT_INTENT_FALLBACK):\n  " + "\n  ".join(violations[:20])
    )
