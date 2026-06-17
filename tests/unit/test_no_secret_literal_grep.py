"""Pin test — prevent ``innocom.vn123`` secret literal from re-entering repo.

CLAUDE.md sacred rule "Tenant-identifier / secret literals — CẤM HOÀN TOÀN
trong file tracked" forbids hard-coded passwords / API keys / DSNs in
any tracked ``.py / .md / .json / .yml / .yaml / .sh / .toml / .cfg /
.ini``.

2026-05-21 cuối phiên audit phát hiện ``innocom.vn123`` (DB password
dev server) rải rác **12 file** plans + reports — pre-existing 2-12
ngày, ship trước session em. Anti-pattern audit 2026-05-19 đã flag là
P0 HIGH nhưng chưa ai fix.

Phase 2 scrub plan ``plans/260521-3FIX-CLEANUP/plan.md`` replace
literal → ``$DB_PASSWORD`` env var + ``<DB_HOST>`` placeholder.

This test is the regression guard: any future commit that re-introduces
``innocom.vn123`` will fail the test before landing on main.

Scope: git-tracked ``.py / .md / .sh / .yml / .yaml / .toml / .cfg /
.ini / .json`` under repo root. Untracked local files
(``.claude/settings.local.json``, ``_archived/`` scratch) are
operator-private and out of scope — only commits / PRs / main are
guarded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit._helpers_git import git_working_tree_available

# The exact literal we are guarding against. If a future need genuinely
# requires the string (e.g. a security audit doc analyzing the leak
# itself), the audit doc should reference it via a placeholder
# ``<REDACTED>`` and add an allowlist comment here.
_FORBIDDEN_LITERAL: str = "innocom.vn123"

# Repo root — three parents up from this file (tests/unit/test_*.py).
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not git_working_tree_available(_REPO_ROOT),
    reason="requires a git working tree (scans `git ls-files` output)",
)

# File extensions we scan. Limited to text formats where a literal
# password would actually live; we do not scan binaries.
_SCAN_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".sh",
        ".yml",
        ".yaml",
        ".toml",
        ".cfg",
        ".ini",
        ".json",
    },
)


def _tracked_files() -> list[Path]:
    """Return absolute paths of all git-tracked files in repo.

    Scope is git-tracked only — the CLAUDE.md sacred rule applies to
    tracked files (what lands in commits / PRs / main). Untracked
    local files (e.g. ``.claude/settings.local.json``, ``_archived/``
    scratch dirs) are operator-private and out of scope.
    """
    result = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [_REPO_ROOT / line for line in result.stdout.splitlines() if line]


def _should_scan(path: Path) -> bool:
    """Return True when ``path`` is a text file we care about."""
    if not path.is_file():
        return False
    return path.suffix in _SCAN_EXTENSIONS


def test_no_secret_literal_in_tracked_files() -> None:
    """Repo MUST NOT contain the literal ``innocom.vn123``.

    Fails listing each offending file:line so the operator can spot
    the regression source immediately. The test itself is the only
    allowed home for the literal (string assignment to the constant
    above) — we read the constant via ``_FORBIDDEN_LITERAL`` reference
    to keep the literal out of the file body's grep target.
    """
    self_path = Path(__file__).resolve()
    offenders: list[str] = []
    for path in _tracked_files():
        if not _should_scan(path):
            continue
        # Skip this test file itself — the literal lives in
        # ``_FORBIDDEN_LITERAL`` by design.
        if path.resolve() == self_path:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _FORBIDDEN_LITERAL in text:
            # Find the first matching line for a precise error message.
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _FORBIDDEN_LITERAL in line:
                    rel = path.relative_to(_REPO_ROOT)
                    offenders.append(f"  {rel}:{lineno}")
                    break

    assert not offenders, (
        "Secret literal regression — the following tracked files contain "
        f"the forbidden DB password (per CLAUDE.md sacred rule):\n"
        + "\n".join(offenders)
        + "\n\nFix: replace literal with ``$DB_PASSWORD`` env var (shell) or "
        "``<DB_PASSWORD>`` placeholder (docs). See "
        "``plans/260521-3FIX-CLEANUP/plan.md`` for the scrub pattern."
    )
