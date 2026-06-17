"""Pin test — sysprompt .template files must stay domain-neutral.

CLAUDE.md sacred (Domain-neutral rule) + Tenant-identifier rule forbid
brand / customer / tenant literals in any tracked file. Sysprompt
content for a real bot legitimately contains brand names (it goes
into the LLM prompt of that bot), but the *tracked* template file
must redact those literals to placeholder tags (``<bot-brand-name>``,
``<bot-hotline>``, ``<brand-tech-N>``, etc.).

An earlier audit found a tracked sysprompt body file under
``plans/`` carrying five occurrences of a real brand name + the
real hotline + three brand technology names. This pin test guards
against any future tracked ``*.template`` sysprompt file
reintroducing the same literals.

Scope: every git-tracked file matching ``**/*.template`` under
``plans/`` and ``docs/``. Files in ``/tmp`` / ``.claude/`` are
operator-local and out of scope.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit._helpers_git import git_working_tree_available


# Forbidden literal patterns that would have leaked customer / brand
# identity into a sysprompt template. Match is case-sensitive; if a
# future bot legitimately uses these terms in template form, the
# template author should use a placeholder + extend the allowlist
# with a comment justifying the exception.
_FORBIDDEN_LITERALS: tuple[str, ...] = (
    "Dr. Medispa",
    "Dr.Medispa",
    "0926.559.268",
)


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not git_working_tree_available(_REPO_ROOT),
    reason="requires a git working tree (scans `git ls-files` output)",
)


def _tracked_template_files() -> list[Path]:
    """Return absolute paths of git-tracked ``*.template`` files in repo."""
    result = subprocess.run(
        ["git", "ls-files", "--", "*.template"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        (_REPO_ROOT / line).resolve()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def test_no_brand_literal_in_template_files() -> None:
    """Every tracked ``*.template`` must scrub brand / tenant literals."""
    leaks: list[tuple[Path, str]] = []
    for path in _tracked_template_files():
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for literal in _FORBIDDEN_LITERALS:
            if literal in content:
                leaks.append((path.relative_to(_REPO_ROOT), literal))
    assert not leaks, (
        "Brand literal leak in tracked .template files. Replace with "
        f"<placeholder> tags. Found: {leaks}"
    )


def test_template_files_use_placeholder_tags() -> None:
    """Every ``*.template`` must declare its placeholder contract — at
    least one ``<placeholder>`` tag — so a downstream operator knows
    how to instantiate it. Empty templates / templates with zero tags
    point to either a redundant file or an accidental concrete copy.
    """
    templates = _tracked_template_files()
    if not templates:  # No templates yet → nothing to assert.
        return
    missing: list[Path] = []
    for path in templates:
        content = path.read_text(encoding="utf-8", errors="replace")
        if "<" not in content or ">" not in content:
            missing.append(path.relative_to(_REPO_ROOT))
    assert not missing, (
        "Template files without any placeholder tag (likely a concrete "
        f"copy that should be either parameterised or moved off-repo): {missing}"
    )
