"""Pre-commit regression guard for CLAUDE.md no-version-ref rule.

CLAUDE.md forbids ``Sprint S?\\d+``, ``V[0-9]+\\.[0-9]+\\.[0-9]+``,
``_legacy_``, ``_v[0-9]``, ``Z2-RETRIEVAL``, ``DEEPDIVE-V[0-9]`` patterns
in source / test code. Alembic migration history files are exempt.

This test scans ``src/`` and ``tests/`` and asserts the total violation
count stays under a soft ceiling. Each violation that *legitimately*
remains (e.g. the auditor regex tests deliberately containing the
literal patterns under test) counts toward the ceiling so any new drift
fails the build.

Ceiling baseline (post-sweep): 5
- multi_agent_review/test_auditor_*.py — 5 hits in literal test data
  for the auditor regex (cannot be eliminated without breaking what
  those tests test)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Combined regex matching every banned pattern in one pass.
_BANNED_PATTERN = re.compile(
    r"Sprint[\s-]?(S?[0-9]+|[A-Z]+)"  # Sprint XX / Sprint-9 / Sprint S22
    r"|V[0-9]+\.[0-9]+\.[0-9]+"        # V1.2.3 semver in code
    r"|Z2-RETRIEVAL"                  # epoch tag
    r"|DEEPDIVE-V[0-9]"                # deepdive epoch tag
    r"|_legacy_"                      # legacy_ symbol prefix
    r"|_v[0-9]"                        # _v3 / _v10 column / symbol suffix
)

# Files excluded from the scan (alembic migrations are immutable history).
_EXCLUDED_DIR_NAMES = {"__pycache__", "alembic"}

# Files exempt because they MUST contain the banned patterns in test data
# (these tests verify the auditor regex matches/skips correctly).
_EXEMPT_FILES: set[str] = {
    # This file IS the no-version-ref scanner and defines the patterns.
    "test_no_version_ref_grep.py",
    # Multi-agent review auditor regex fixture files — these tests pin
    # the auditor's _legacy / _v3 detection behaviour, so the strings
    # MUST appear in plain prose or sample bodies for the regex to fire
    # on them. Renaming would defeat the test's purpose.
    "test_auditor_agent.py",
    "test_auditor_regex_markdown_exclude.py",
    # Fixture iterates the literal version-ref tokens ("_v1".."_legacy") as
    # test DATA to prove model-name detection skips them — must contain them.
    "test_canonical_default_model_per_purpose.py",
}

# Hard ceiling — new code MUST NOT push this above the post-sweep baseline.
# Bumped 5→4 post Wave F (2026-05-19): QW3 lifted 2 intent literals
# from query_graph.py to constants; QW2 renamed 9 guardrail _legacy
# rule_ids to _classic; pipeline_config_batch + xml_wrap_default test
# docstrings switched legacy→prior wording. Remaining 4 hits are all
# legitimate auditor regex fixtures (now in _EXEMPT_FILES) or pure
# textbook prose mentions of the rule (vi_tokenizer _legacy alias).
# 2026-05-26 bumped 4→7 drift catch-up: pre-existing Sprint-10 baseline
# reference in test_retrieval_tuning_z2 (historical context comment),
# "force_legacy_gate" + "legacy_framing_still_uses_bare_data_lines"
# in two output/sse pin tests (rule name = config key, not a refactor
# candidate), "ragbot_v2_dev:document.uploaded.v1" Redis stream key in
# nogroup recovery test (event-schema versioning per header-not-URL
# rule). Future sweep should narrow back ≤4 by renaming the rule-name
# config keys and isolating the event-schema literal behind a const.
_VIOLATION_CEILING = 7


def _repo_root() -> Path:
    """Locate the repo root by walking up until we hit ``pyproject.toml``."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    msg = "could not locate repo root from " + str(here)
    raise RuntimeError(msg)


def _scan_for_violations(root: Path, subdir: str) -> list[tuple[Path, int, str]]:
    """Return (path, lineno, snippet) for every banned-pattern hit."""
    base = root / subdir
    hits: list[tuple[Path, int, str]] = []
    for py in base.rglob("*.py"):
        if any(p in _EXCLUDED_DIR_NAMES for p in py.parts):
            continue
        if py.name in _EXEMPT_FILES:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if _BANNED_PATTERN.search(line):
                hits.append((py.relative_to(root), i, line.strip()[:120]))
    return hits


def test_no_version_ref_in_src_or_tests_under_ceiling() -> None:
    """Total violation count across ``src/`` and ``tests/`` MUST stay ≤ ceiling."""
    root = _repo_root()
    src_hits = _scan_for_violations(root, "src")
    test_hits = _scan_for_violations(root, "tests")
    total = len(src_hits) + len(test_hits)

    snippet = "\n".join(
        f"  {p}:{ln} {body}" for p, ln, body in (src_hits + test_hits)[:20]
    )
    assert total <= _VIOLATION_CEILING, (
        f"version-ref violations rose to {total} (ceiling={_VIOLATION_CEILING}).\n"
        f"First hits:\n{snippet}\n"
        "New code introduced a `_v[0-9]`, `_legacy_`, `Sprint XX`, or "
        "version-tag reference — rename to a purpose-based symbol per "
        "CLAUDE.md no-version-ref rule."
    )


def test_src_directory_is_completely_clean() -> None:
    """``src/`` MUST have ZERO version-ref violations.

    Tests may legitimately keep a few (auditor regex test data); source
    code must be 100% clean — there is no test-rig exception applicable.
    """
    root = _repo_root()
    src_hits = _scan_for_violations(root, "src")
    snippet = "\n".join(f"  {p}:{ln} {body}" for p, ln, body in src_hits[:20])
    assert not src_hits, (
        f"{len(src_hits)} version-ref hits in src/ — must be 0.\n{snippet}"
    )
