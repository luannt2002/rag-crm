"""Integration tests for scripts/pre-commit-hook.sh.

Each test creates a fresh temp git repo, stages a synthetic
src/ragbot/<file>.py blob, runs the hook script, and asserts the exit
code + which violation rule was reported (or PASS).

The hook lives outside src/ragbot, so we exercise it via subprocess —
not by importing Python helpers. That mirrors how git itself invokes it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root resolves relative to this test file. The hook lives at
# scripts/pre-commit-hook.sh of the repo. Tests must NOT depend on the
# CWD they are launched from.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK_PATH = _REPO_ROOT / "scripts" / "pre-commit-hook.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a git command inside `repo`."""
    full_env = os.environ.copy()
    # Force git to ignore any global hooksPath config the dev box may have.
    full_env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    full_env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    full_env["HOME"] = str(repo)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


def _make_repo(tmp_path: Path) -> Path:
    """Initialise a fresh git repo, copy the hook script, return the repo path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")

    # Mirror the on-disk hook into the temp repo so the script itself
    # exercises `git rev-parse --show-toplevel` correctly.
    hook_dst_dir = repo / "scripts"
    hook_dst_dir.mkdir()
    shutil.copy(_HOOK_PATH, hook_dst_dir / "pre-commit-hook.sh")
    (hook_dst_dir / "pre-commit-hook.sh").chmod(0o755)
    return repo


def _stage_python(repo: Path, rel_path: str, content: str) -> None:
    """Write content under `repo/<rel_path>` and `git add` it."""
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)


def _run_hook(repo: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the hook from within the repo and capture exit + output."""
    full_env = os.environ.copy()
    full_env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    full_env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    full_env["HOME"] = str(repo)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", "scripts/pre-commit-hook.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hook_pass_on_clean_staged_file(tmp_path: Path) -> None:
    """No violations -> exit 0."""
    repo = _make_repo(tmp_path)
    clean = (
        '"""Clean module — no magic numbers, no model literals, no inline intents."""\n'
        "from ragbot.shared.constants import DEFAULT_TOP_K\n"
        "\n"
        "def f(x: int) -> int:\n"
        "    return x + DEFAULT_TOP_K\n"
    )
    _stage_python(repo, "src/ragbot/clean_mod.py", clean)
    result = _run_hook(repo)
    assert result.returncode == 0, f"expected PASS, got: {result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout


def test_hook_fail_on_magic_number(tmp_path: Path) -> None:
    """Magic number 1024 outside constants.py -> exit 1, magic-number rule."""
    repo = _make_repo(tmp_path)
    bad = (
        '"""Bad module with magic number."""\n'
        "def chunk(text):\n"
        "    return text[:1024]\n"
    )
    _stage_python(repo, "src/ragbot/bad_magic.py", bad)
    result = _run_hook(repo)
    assert result.returncode == 1, f"expected FAIL, got: {result.stdout}"
    assert "magic-number" in result.stdout
    assert "1024" in result.stdout


def test_hook_fail_on_model_literal(tmp_path: Path) -> None:
    """Hardcoded \"gpt-4\" string -> exit 1, model-literal rule."""
    repo = _make_repo(tmp_path)
    bad = (
        '"""Bad module with model literal."""\n'
        'MODEL = "gpt-4-turbo"\n'
    )
    _stage_python(repo, "src/ragbot/bad_model.py", bad)
    result = _run_hook(repo)
    assert result.returncode == 1, f"expected FAIL, got: {result.stdout}"
    assert "model-literal" in result.stdout


def test_hook_fail_on_inline_intent_outside_dto(tmp_path: Path) -> None:
    """Inline 'factoid' in a non-DTO file -> exit 1, inline-intent rule."""
    repo = _make_repo(tmp_path)
    bad = (
        '"""Orchestrator module with inline intent literal."""\n'
        "def classify(x):\n"
        "    if x == 'factoid':\n"
        "        return True\n"
        "    return False\n"
    )
    _stage_python(repo, "src/ragbot/orchestration/bad_intent.py", bad)
    result = _run_hook(repo)
    assert result.returncode == 1, f"expected FAIL, got: {result.stdout}"
    assert "inline-intent" in result.stdout


def test_hook_fail_on_broad_except_without_noqa(tmp_path: Path) -> None:
    """`except Exception:` with no noqa -> exit 1, broad-except rule."""
    repo = _make_repo(tmp_path)
    bad = (
        '"""Bad module — bare broad-except."""\n'
        "def run():\n"
        "    try:\n"
        "        do_something()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    _stage_python(repo, "src/ragbot/application/bad_except.py", bad)
    result = _run_hook(repo)
    assert result.returncode == 1, f"expected FAIL, got: {result.stdout}"
    assert "broad-except" in result.stdout


def test_hook_pass_on_broad_except_with_noqa(tmp_path: Path) -> None:
    """`except Exception:  # noqa: BLE001 — <reason>` -> exit 0."""
    repo = _make_repo(tmp_path)
    good = (
        '"""Module with justified broad-except."""\n'
        "def run():\n"
        "    try:\n"
        "        do_something()\n"
        "    except Exception:  # noqa: BLE001 - top-level worker recovery\n"
        "        pass\n"
    )
    _stage_python(repo, "src/ragbot/workers/justified_except.py", good)
    result = _run_hook(repo)
    assert result.returncode == 0, f"expected PASS, got: {result.stdout}\n{result.stderr}"


def test_hook_bypass_env_var(tmp_path: Path) -> None:
    """RAGBOT_PRECOMMIT_BYPASS=1 -> exit 0 even with violations + warning to stderr."""
    repo = _make_repo(tmp_path)
    bad = (
        '"""Will-be-bypassed module."""\n'
        "def chunk(text):\n"
        "    return text[:1024]\n"  # magic number that would normally fail
    )
    _stage_python(repo, "src/ragbot/bypass_me.py", bad)
    result = _run_hook(repo, env={"RAGBOT_PRECOMMIT_BYPASS": "1"})
    assert result.returncode == 0, f"bypass should force PASS, got: {result.stdout}"
    assert "BYPASSED" in result.stderr
