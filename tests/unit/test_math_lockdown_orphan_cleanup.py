"""math_lockdown orphan config is gone and stays gone (P2-A/P2-E ↔️ / D6).

The app-side math override was removed (6e9041d / cad52dc); the three
``system_config`` rows that survived are deleted by alembic 0200. These
guards keep the DB-reflects-code invariant: no code reads the flag, no seed
re-introduces it, and the doc no longer advertises a dead override.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_no_code_reads_math_lockdown_enabled() -> None:
    """No production reader of the removed flag (it would be a dead branch
    or, worse, a re-introduced sacred-violating override)."""
    hits: list[str] = []
    for path in (_ROOT / "src").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("math_lockdown_enabled", "default_math_lockdown_enabled"):
            if needle in text:
                hits.append(f"{path.relative_to(_ROOT)}: {needle}")
    assert not hits, f"math_lockdown flag readers re-introduced: {hits}"


def test_init_seed_does_not_reintroduce_math_lockdown() -> None:
    seed = (_ROOT / "scripts" / "init_system_config.py").read_text(encoding="utf-8")
    assert "math_lockdown" not in seed, (
        "init_system_config must not re-seed the deleted math_lockdown rows"
    )


def test_migration_0200_deletes_and_is_reversible() -> None:
    src = (
        _ROOT / "alembic" / "_archive_pre_squash_20260618"
        / "20260610_0200_delete_math_lockdown_orphan_config.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0199"' in src
    assert "DELETE FROM system_config WHERE key IN" in src
    # Reversible — downgrade restores the rows.
    assert "INSERT INTO system_config" in src


def test_doc_04d_no_longer_advertises_math_lockdown() -> None:
    doc = (
        _ROOT / "docs" / "master" / "04-D-pipeline-orchestration.md"
    ).read_text(encoding="utf-8")
    # The GENERATE diagram must not show "math lockdown" as a live step.
    assert "→ math lockdown" not in doc
    assert "citation parse → math lockdown" not in doc
