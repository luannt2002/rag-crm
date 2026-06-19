"""Migration 0050 regression: type-text comparison, no atttypmod arithmetic.

Incident 2026-04-29: a prior draft of this migration computed
`dim = atttypmod - 4`, but pgvector stores `atttypmod = N` directly
(not N+4). On already-1536 columns the predicate `dim == 1536`
became false, triggering ALTER ... USING NULL that wiped 95
production embeddings. Restored via re-embed.

The corrected migration uses `format_type(atttypid, atttypmod)` —
Postgres's own type-name formatter — and compares the resulting
TEXT (`'vector(1536)'`) against constants. No arithmetic.
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATION = _REPO_ROOT / "alembic/_archive_pre_squash_20260618/20260429_0050_vector_dim_1024_to_1536.py"


def _src() -> str:
    return _MIGRATION.read_text()


def test_migration_uses_format_type_not_arithmetic() -> None:
    src = _src()
    assert "format_type(a.atttypid, a.atttypmod)" in src


def test_migration_does_not_use_atttypmod_arithmetic() -> None:
    """Forbid arithmetic patterns in EXECUTABLE code (allowed in docstring
    incident note)."""
    src = _src()
    # Strip the module docstring + per-block triple-string docstrings.
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # Code-only check: skip any node that IS a string-only stmt.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Mod, ast.Sub)):
            # We don't have full subtree text — fall through to text scan
            # below with an exclusion list of comment lines.
            pass
    # Cheap & strict: scan non-comment, non-docstring lines.
    code_lines = []
    in_doc = False
    doc_marker = None
    for line in src.splitlines():
        stripped = line.strip()
        if not in_doc:
            for marker in ('"""', "'''"):
                if stripped.startswith(marker):
                    if stripped.count(marker) >= 2 and len(stripped) > len(marker):
                        # Single-line docstring — skip just this line.
                        break
                    in_doc = True
                    doc_marker = marker
                    break
            else:
                if not stripped.startswith("#"):
                    code_lines.append(line)
        else:
            if doc_marker and doc_marker in stripped:
                in_doc = False
                doc_marker = None
    code_only = "\n".join(code_lines)
    for pat in ("atttypmod - 4", "atttypmod + 4", "row - 4"):
        assert pat not in code_only, f"forbidden arithmetic pattern in code: {pat!r}"


def test_migration_compares_text_constants() -> None:
    src = _src()
    assert 'TARGET_TYPE = "vector(1536)"' in src
    assert 'LEGACY_TYPE = "vector(1024)"' in src


def test_migration_refuses_to_alter_unknown_dim() -> None:
    src = _src()
    assert "Refusing to ALTER USING NULL" in src
    assert "raise RuntimeError" in src


def test_migration_idempotent_skip_on_target_match() -> None:
    src = _src()
    assert "if cur == TARGET_TYPE:" in src


def test_migration_recreates_hnsw_at_target() -> None:
    src = _src()
    assert "USING hnsw" in src
    assert "vector_cosine_ops" in src
    assert "m = 16" in src
    assert "ef_construction = 64" in src


def test_migration_has_incident_note() -> None:
    src = _src()
    assert "INCIDENT NOTE" in src
