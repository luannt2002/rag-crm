"""Regression test for fix-260516-multi-head-alembic — 010a merge migration.

Pre-fix: ``script.get_heads()`` returns 2 heads (0107c + 0109) → ``alembic
upgrade head`` would fail with MultipleHeadRevisions.

Post-fix: 010a merge revision rejoins the DAG → single head ``010a``.
"""
from __future__ import annotations

import pathlib

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(cfg)


def test_alembic_chain_has_single_head_post_010a(
    script_directory: ScriptDirectory,
) -> None:
    """Post-merge migration: exactly one head exists in the DAG.

    010a was the original merge revision; subsequent migrations append
    linearly so the head moves forward. The invariant we still need
    to enforce is a SINGLE head — any future multi-head accident must
    fail this test.
    """
    heads = script_directory.get_heads()
    assert len(heads) == 1, (
        f"Expected single head after 010a merge migration, "
        f"got {len(heads)}: {heads!r}"
    )


def test_010a_merge_revision_lists_both_parents(
    script_directory: ScriptDirectory,
) -> None:
    """010a's down_revision must contain both 0107c and 0109 (DAG re-join)."""
    rev = script_directory.get_revision("010a")
    parents = rev.down_revision
    assert isinstance(parents, tuple), (
        f"010a.down_revision must be a tuple of multiple parents for a merge "
        f"migration, got {type(parents).__name__}: {parents!r}"
    )
    assert set(parents) == {"0107c", "0109"}, (
        f"010a must merge exactly 0107c and 0109, got {set(parents)!r}"
    )


def test_010a_upgrade_downgrade_are_noops(
    script_directory: ScriptDirectory,
) -> None:
    """010a is a pure DAG re-join — no DDL in upgrade or downgrade."""
    rev = script_directory.get_revision("010a")
    module = rev.module
    upgrade_src = module.upgrade.__code__.co_consts
    downgrade_src = module.downgrade.__code__.co_consts
    # A pure no-op function compiles to const tuple = (None,) or
    # (docstring, None). Anything else would mean DDL was added.
    assert all(c is None or isinstance(c, str) for c in upgrade_src), (
        f"010a.upgrade() must be pure no-op (only docstring + return None); "
        f"got consts: {upgrade_src!r}"
    )
    assert all(c is None or isinstance(c, str) for c in downgrade_src), (
        f"010a.downgrade() must be pure no-op; got consts: {downgrade_src!r}"
    )
