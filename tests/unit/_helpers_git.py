"""Shared skip guard for tests that require a real git working tree.

Several guard tests shell out to ``git`` (``git ls-files`` to enumerate
tracked files, ``git rev-parse`` / worktree checks). In an environment that
is not a git checkout (e.g. a deployed copy or a CI image without the .git
dir) those tests cannot run — they should SKIP, not FAIL, so a missing git
tree is never mistaken for a real regression. Not a test module (no ``test_``
prefix) so pytest does not collect it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_working_tree_available(repo_root: Path) -> bool:
    """Return True when *repo_root* is inside a usable git working tree."""
    if shutil.which("git") is None:
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"
