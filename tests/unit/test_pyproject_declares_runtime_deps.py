"""Z4-P0 regression: production imports MUST be declared in pyproject.toml.

Audit `AUDIT_DEEPDIVE_Z4_TESTS_DEADCODE_20260429_144549.md` (P0):
PyJWT was imported by `JwtTokenService` but only `types-pyjwt` (the
type-stub package) appeared in pyproject.toml. A fresh
`pip install -e .` failed at runtime with `ModuleNotFoundError`.
Currently masked because dev venvs were patched manually.

Same risk for: psycopg2-binary (Alembic), openpyxl (Excel parser),
underthesea (VN tokenizer).

This test parses pyproject.toml and asserts each import the source
tree actually uses appears in the dependency list.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

# Python 3.11+ ships tomllib in stdlib; this project pins 3.12+.
import tomllib


_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _load_deps() -> set[str]:
    """Return the set of declared runtime dependency PACKAGE NAMES."""
    pyproject = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())
    deps_lines = pyproject["project"]["dependencies"]
    names: set[str] = set()
    for line in deps_lines:
        # Strip extras and version specifiers: `package[extra]>=1.0.0` → `package`
        m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
        if m:
            names.add(m.group(1).lower())
    return names


def _file_contains_import(rel_path: str, *needles: str) -> bool:
    p = _PROJECT_ROOT / rel_path
    if not p.exists():
        return False
    src = p.read_text()
    return any(n in src for n in needles)


@pytest.mark.parametrize(
    ("import_name", "dep_name", "evidence_path", "evidence_strings"),
    [
        (
            "jwt",
            "pyjwt",
            "src/ragbot/application/services/jwt_token_service.py",
            ("import jwt", "from jwt"),
        ),
        (
            "psycopg2",
            "psycopg2-binary",
            "alembic/env.py",
            ("psycopg2",),
        ),
        (
            "openpyxl",
            "openpyxl",
            "src/ragbot/infrastructure/parser/excel_openpyxl_parser.py",
            ("openpyxl",),
        ),
        (
            "underthesea",
            "underthesea",
            "src/ragbot/shared/vi_tokenizer.py",
            ("underthesea",),
        ),
    ],
)
def test_runtime_dep_declared(
    import_name: str,
    dep_name: str,
    evidence_path: str,
    evidence_strings: tuple[str, ...],
) -> None:
    """Each tuple ↔ a Python import that must be declared as a runtime dep."""
    if not _file_contains_import(evidence_path, *evidence_strings):
        pytest.skip(
            f"evidence file {evidence_path} no longer references {import_name!r} — "
            f"either the import was removed (in which case drop {dep_name} from "
            f"pyproject.toml) or the path moved (update this test).",
        )
    declared = _load_deps()
    assert dep_name.lower() in declared, (
        f"{import_name!r} is imported by {evidence_path} but {dep_name!r} is "
        f"NOT in pyproject.toml [project.dependencies]. Fresh install will "
        f"ImportError at runtime."
    )


def test_types_pyjwt_lives_in_dev_extras_not_runtime() -> None:
    """`types-pyjwt` is a type-stub package and belongs in [dev], not the
    runtime dependency list. Putting it in runtime deps confused a prior
    auditor into thinking PyJWT was declared."""
    pyproject = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())
    runtime_deps = pyproject["project"]["dependencies"]
    dev_deps = pyproject["project"]["optional-dependencies"]["dev"]

    assert not any("types-pyjwt" in d.lower() for d in runtime_deps), (
        "types-pyjwt is a type stub — must NOT appear in runtime deps"
    )
    assert any("types-pyjwt" in d.lower() for d in dev_deps), (
        "types-pyjwt should be in [dev] for type-checking"
    )


def test_pyjwt_actually_importable_in_current_env() -> None:
    """Sanity check the dev environment matches the declaration."""
    try:
        import jwt  # noqa: F401
    except ModuleNotFoundError:
        pytest.fail(
            "pyjwt is declared in pyproject.toml but not installed in this "
            "environment — run `pip install -e .` to sync.",
        )
