"""Smoke + behaviour coverage for ``scripts/anti_hardcode_check.sh``.

MoM 00c-analytics — verify the pre-commit hook:
  1. Syntax of the bash script is valid.
  2. Empty / clean src tree → PASS (rc 0).
  3. Inline magic number on a config-shaped name → FAIL (rc 1).
  4. ``mock_data = [...]`` literal in production → FAIL (rc 1).
  5. ``TODO`` comment crumb → FAIL (rc 1).
  6. Hardcoded model literal (``"gpt-4"``) → FAIL (rc 1).
  7. Import from ``shared.constants`` with same magic name → PASS (rc 0).

Each test stages a synthetic ``src/ragbot/<file>.py`` under a temp dir,
points the hook at it via ``--src-root``, and asserts the exit code.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "scripts" / "anti_hardcode_check.sh"


def _run(src_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HOOK), "--src-root", str(src_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _stage_pkg(tmp_path: Path, filename: str, body: str) -> Path:
    """Create a minimal src/ragbot/<filename> tree under tmp_path."""
    pkg = tmp_path / "src" / "ragbot"
    pkg.mkdir(parents=True, exist_ok=True)
    target = pkg / filename
    target.write_text(body, encoding="utf-8")
    return pkg


def test_hook_exists_and_executable() -> None:
    assert HOOK.exists(), f"hook missing: {HOOK}"


def test_bash_syntax_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(HOOK)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_clean_tree_passes(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "clean.py",
        "from ragbot.shared.constants import DEFAULT_X\n"
        "value = DEFAULT_X\n",
    )
    result = _run(src_root)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_inline_magic_number_fails(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "leaky.py",
        "class C:\n"
        "    def __init__(self):\n"
        "        self.timeout = 30\n",
    )
    result = _run(src_root)
    assert result.returncode == 1
    assert "magic number" in result.stderr.lower()


def test_mock_literal_fails(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "fixtures.py",
        "mock_data = [1, 2, 3]\n",
    )
    result = _run(src_root)
    assert result.returncode == 1
    assert "mock" in result.stderr.lower()


def test_todo_comment_fails(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "todo.py",
        "def f():\n"
        "    # TODO: clean this up later\n"
        "    return None\n",
    )
    result = _run(src_root)
    assert result.returncode == 1
    assert "todo" in result.stderr.lower() or "comment" in result.stderr.lower()


def test_model_literal_fails(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "model_pin.py",
        'MODEL = "gpt-4-turbo"\n',
    )
    result = _run(src_root)
    assert result.returncode == 1
    assert "model" in result.stderr.lower()


def test_imported_constant_passes(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "good.py",
        "from ragbot.shared.constants import DEFAULT_LLM_TIMEOUT_S\n"
        "def make_client():\n"
        "    return Client(timeout=DEFAULT_LLM_TIMEOUT_S)\n",
    )
    result = _run(src_root)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_os_getenv_passes(tmp_path: Path) -> None:
    src_root = _stage_pkg(
        tmp_path,
        "envy.py",
        "import os\n"
        "def fetch():\n"
        "    timeout = int(os.getenv('TIMEOUT', '30'))\n"
        "    return timeout\n",
    )
    result = _run(src_root)
    assert result.returncode == 0, (result.stdout, result.stderr)
