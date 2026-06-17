"""Unit tests for scripts/cleanup_old_worktrees.sh + scripts/perf_baseline.sh.

These tests guard the two operator scripts shipped by Agent A3-cont wave 2:
existence, executable bit, and bash syntax. We avoid invoking the real
``git worktree remove`` path here — dry-run defaults of the script are
the integration surface and are exercised separately by ops.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.unit._helpers_git import git_working_tree_available

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEANUP_SCRIPT = REPO_ROOT / "scripts" / "cleanup_old_worktrees.sh"
BASELINE_SCRIPT = REPO_ROOT / "scripts" / "perf_baseline.sh"

pytestmark = pytest.mark.skipif(
    not git_working_tree_available(REPO_ROOT),
    reason="requires a git working tree (worktree cleanup shells out to git)",
)


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def test_cleanup_script_exists_and_executable() -> None:
    assert CLEANUP_SCRIPT.exists(), f"missing: {CLEANUP_SCRIPT}"
    assert _is_executable(CLEANUP_SCRIPT), (
        f"not executable (chmod +x missing): {CLEANUP_SCRIPT}"
    )


def test_cleanup_script_bash_syntax_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(CLEANUP_SCRIPT)],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed: {result.stderr.decode(errors='replace')}"
    )


def test_cleanup_script_rejects_bad_mode() -> None:
    """Passing an invalid mode must exit non-zero with a clear message."""
    result = subprocess.run(
        ["bash", str(CLEANUP_SCRIPT), "main", "7", "force-wipe"],
        capture_output=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert b"mode must be" in result.stderr


def test_cleanup_script_dry_run_default_runs() -> None:
    """Dry-run with main base should exit cleanly (lists or empty)."""
    env = os.environ.copy()
    # Be defensive against shells that bring in unset variables.
    result = subprocess.run(
        ["bash", str(CLEANUP_SCRIPT), "main"],
        capture_output=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=30,
    )
    # 0 = success; 1 is acceptable only if git complains (no worktrees, etc).
    assert result.returncode in (0, 1), (
        f"unexpected exit={result.returncode}\n"
        f"stdout={result.stdout.decode(errors='replace')[:400]}\n"
        f"stderr={result.stderr.decode(errors='replace')[:400]}"
    )
    # Smoke-check: at least one of our log lines should appear on success.
    if result.returncode == 0:
        out = result.stdout.decode(errors="replace")
        assert "cleanup_old_worktrees" in out


def test_perf_baseline_script_exists_and_executable() -> None:
    assert BASELINE_SCRIPT.exists(), f"missing: {BASELINE_SCRIPT}"
    assert _is_executable(BASELINE_SCRIPT), (
        f"not executable: {BASELINE_SCRIPT}"
    )


def test_perf_baseline_script_bash_syntax_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(BASELINE_SCRIPT)],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed: {result.stderr.decode(errors='replace')}"
    )
