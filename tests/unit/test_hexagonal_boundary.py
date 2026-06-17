"""Hexagonal-boundary gate — application/ MUST NOT import infrastructure/.

Walks every ``.py`` file under ``src/ragbot/application/`` and parses
its AST. Any ``import`` or ``from`` node whose module starts with
``ragbot.infrastructure`` is a violation.

A grandfather allowlist (``_hexagonal_boundary_allowlist.txt``) records
files that still violate; the test fails when:

  (a) a NEW file imports infrastructure (one not on the allowlist), OR
  (b) an allowlisted file no longer violates (so the allowlist can
      shrink rather than rot).

Issue #7 in ``RAG_Master_of_Masters_DeepDive_Report.md`` owns this.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "src" / "ragbot" / "application"
ALLOWLIST_PATH = Path(__file__).with_name("_hexagonal_boundary_allowlist.txt")

INFRA_PREFIX = "ragbot.infrastructure"


def _load_allowlist() -> set[str]:
    """Parse the allowlist file — one relative path per line, `#` comments."""
    out: set[str] = set()
    if not ALLOWLIST_PATH.exists():
        return out
    for raw in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        # Strip inline comment, then whitespace.
        text = raw.split("#", 1)[0].strip()
        if text:
            out.add(text)
    return out


def _violating_imports(py_path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, module)]`` for each infrastructure import in ``py_path``.

    Catches both top-level and function-local imports — AST traversal is
    full-tree, not just ``tree.body``.
    """
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except SyntaxError as exc:  # pragma: no cover — only fires on broken source
        pytest.fail(f"Could not parse {py_path}: {exc}")

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == INFRA_PREFIX or mod.startswith(f"{INFRA_PREFIX}."):
                hits.append((node.lineno, mod))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == INFRA_PREFIX or alias.name.startswith(
                    f"{INFRA_PREFIX}.",
                ):
                    hits.append((node.lineno, alias.name))
    return hits


def _iter_app_py_files() -> list[Path]:
    return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_new_infrastructure_imports_in_application() -> None:
    """Fail if any application/ file imports infrastructure/ without being
    on the grandfather allowlist."""
    allowlist = _load_allowlist()
    new_violators: dict[str, list[tuple[int, str]]] = {}

    for py in _iter_app_py_files():
        rel = py.relative_to(REPO_ROOT).as_posix()
        hits = _violating_imports(py)
        if hits and rel not in allowlist:
            new_violators[rel] = hits

    assert not new_violators, (
        "New hexagonal-boundary violation(s) detected. application/ MUST NOT "
        "import infrastructure/. Move the dependency behind a Port in "
        "application/ports/ instead. Offenders:\n"
        + "\n".join(
            f"  {f}: " + ", ".join(f"L{ln}={m}" for ln, m in v)
            for f, v in sorted(new_violators.items())
        )
    )


def test_allowlist_entries_still_violate() -> None:
    """Allowlist must shrink, never rot. If an allowlisted file no longer
    imports infrastructure, that's good news — the entry should be removed
    so future regressions are caught."""
    allowlist = _load_allowlist()
    stale: list[str] = []

    for rel in sorted(allowlist):
        py = REPO_ROOT / rel
        if not py.exists():
            stale.append(f"{rel} (file no longer exists)")
            continue
        if not _violating_imports(py):
            stale.append(f"{rel} (no longer violates — remove from allowlist)")

    assert not stale, (
        "Allowlist entries are stale and should be deleted:\n  "
        + "\n  ".join(stale)
    )


def test_known_clean_services_have_no_infra_imports() -> None:
    """Pin the 5 services fixed by Stream G7. Regression would silently
    re-introduce a boundary leak unless this explicit list flags it."""
    pinned = [
        "src/ragbot/application/services/bot_registry_service.py",
        "src/ragbot/application/services/language_pack_service.py",
        "src/ragbot/application/services/structured_output_helper.py",
        "src/ragbot/application/services/step_tracker.py",
        "src/ragbot/application/services/tenant_rate_limiter.py",
    ]
    failures: dict[str, list[tuple[int, str]]] = {}
    for rel in pinned:
        py = REPO_ROOT / rel
        assert py.exists(), f"Pinned file missing: {rel}"
        hits = _violating_imports(py)
        if hits:
            failures[rel] = hits

    assert not failures, (
        "Pinned-clean file regained an infrastructure import:\n"
        + "\n".join(
            f"  {f}: " + ", ".join(f"L{ln}={m}" for ln, m in v)
            for f, v in failures.items()
        )
    )
