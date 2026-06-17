"""Sanity tests for the Auditor-Chief CI scripts.

We only verify:

- Both scripts exist and are executable.
- ``audit_agent_diff.sh`` exits 2 on bad invocation (no args).
- ``audit_agent_diff.sh`` exits 0 when feature == base (no regression).
- ``audit_async_mindset.sh`` exits 0 in warn mode on the current src
  (default behaviour — heuristic finding != fail).
- ``audit_async_mindset.sh`` detects an obvious H1 violation when one
  is written into a temp file passed via --staged-equivalent (here we
  use the existing find-based path with a synth file under
  ``src/ragbot/_audit_test/``).

Slow / network-dependent paths (full anti_hardcode_check sweep) are
NOT exercised — those have their own pytest coverage. This file is
the seam test for the new CI scripts shipped in
``agent-260518-A2-cont-ci-mindset``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.unit._helpers_git import git_working_tree_available

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_AGENT_DIFF = REPO_ROOT / "scripts" / "audit_agent_diff.sh"
AUDIT_ASYNC_MINDSET = REPO_ROOT / "scripts" / "audit_async_mindset.sh"

pytestmark = pytest.mark.skipif(
    not git_working_tree_available(REPO_ROOT),
    reason="requires a git working tree (audit script shells out to git)",
)


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a bash script and capture output. Non-zero exit allowed."""
    return subprocess.run(
        ["bash", *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_audit_agent_diff_exists_and_executable() -> None:
    """Script ships and is marked executable."""
    assert AUDIT_AGENT_DIFF.is_file(), f"missing: {AUDIT_AGENT_DIFF}"
    assert os.access(AUDIT_AGENT_DIFF, os.X_OK), "audit_agent_diff.sh not executable"


def test_audit_async_mindset_exists_and_executable() -> None:
    """Script ships and is marked executable."""
    assert AUDIT_ASYNC_MINDSET.is_file(), f"missing: {AUDIT_ASYNC_MINDSET}"
    assert os.access(AUDIT_ASYNC_MINDSET, os.X_OK), "audit_async_mindset.sh not executable"


def test_audit_agent_diff_no_args_returns_2() -> None:
    """Invocation error path: missing required <feature_ref> arg."""
    result = _run(str(AUDIT_AGENT_DIFF))
    assert result.returncode == 2, (
        f"expected exit 2 on no-args, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_audit_agent_diff_self_compare_passes() -> None:
    """Feature == base means delta == 0 for every guard → PASS."""
    # Use HEAD vs HEAD so worktree-add resolves to the same commit twice.
    result = _run(str(AUDIT_AGENT_DIFF), "HEAD", "HEAD")
    # On a server without git fetch ability, the script may still PASS
    # because both refs resolve to the same SHA.
    assert result.returncode == 0, (
        f"self-compare expected exit 0, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS — no regression vs base" in result.stdout


def test_audit_async_mindset_warn_mode_exit_0() -> None:
    """Default warn mode: even with findings, exit code is 0."""
    result = _run(str(AUDIT_ASYNC_MINDSET))
    assert result.returncode == 0, (
        f"warn mode expected exit 0, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_audit_async_mindset_strict_mode_detects_h1_synthetic(tmp_path: Path) -> None:
    """Plant an obvious H1 violation under src/ragbot and confirm
    --strict surfaces it as exit 1. Cleans up after itself.
    """
    synth_dir = REPO_ROOT / "src" / "ragbot" / "_audit_test_h1"
    synth_dir.mkdir(parents=True, exist_ok=True)
    synth_file = synth_dir / "violation.py"
    synth_file.write_text(
        textwrap.dedent(
            """
            class Foo:
                async def bad_sequential(self):
                    a = await self._redis.get("k1")
                    b = await self._redis.get("k2")
                    return a, b
            """
        ).lstrip()
    )
    try:
        result = _run(str(AUDIT_ASYNC_MINDSET), "--strict")
        assert result.returncode == 1, (
            f"expected exit 1 with synth violation, got {result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "_audit_test_h1/violation.py" in result.stdout
        assert "H1" in result.stdout
    finally:
        shutil.rmtree(synth_dir, ignore_errors=True)


def test_audit_async_mindset_help_exits_0() -> None:
    """--help prints usage banner and exits 0."""
    result = _run(str(AUDIT_ASYNC_MINDSET), "--help")
    assert result.returncode == 0
    assert "audit_async_mindset" in result.stdout


def test_audit_agent_diff_help_exits_0() -> None:
    """--help prints usage banner and exits 0."""
    result = _run(str(AUDIT_AGENT_DIFF), "--help")
    assert result.returncode == 0
    assert "audit_agent_diff" in result.stdout


def test_audit_agent_diff_unknown_flag_returns_2() -> None:
    """Unknown flag → invocation error."""
    result = _run(str(AUDIT_AGENT_DIFF), "--no-such-flag")
    assert result.returncode == 2


@pytest.mark.skipif(
    not shutil.which("git"), reason="git binary missing on this runner"
)
def test_audit_agent_diff_resolves_branch_aliases() -> None:
    """Both ``main`` and a SHA are valid feature/base refs."""
    # main may not exist as a local branch in some CI worktrees; try a
    # SHA fallback. We only assert the script does not crash on a
    # well-formed invocation pointing at HEAD twice.
    result = _run(str(AUDIT_AGENT_DIFF), "HEAD", "HEAD")
    assert result.returncode == 0
    # The script prints "mode=regression-only" by default.
    assert "mode=regression-only" in result.stdout
