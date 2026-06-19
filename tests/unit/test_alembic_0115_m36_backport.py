"""Pin tests — alembic 0115 backport Wave M3.6 system_config drift keys.

Wave M3.6 (2026-05-20) shipped 4 system_config keys via psql UPSERT
outside alembic. Re-cloning the DB from alembic head alone missed
these, causing silent prod-parity break. 0115 backports them; this
test guards against a future commit silently dropping them.
"""

from __future__ import annotations

from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "_archive_pre_squash_20260618"
    / "20260525_0115_backport_m36_system_config.py"
)


def _read() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_revises_0114() -> None:
    src = _read()
    assert 'revision: str = "0115"' in src
    assert 'down_revision: str | None = "0114"' in src


def test_all_four_drift_keys_seeded() -> None:
    """All four M3.6 keys must appear in _BACKPORT_ROWS."""
    src = _read()
    for key in (
        "speculative_streaming_enabled",
        "grounding_check_async_enabled",
        "pipeline_parallel_output_guards_enabled",
        "grounding_check_threshold_by_intent",
    ):
        assert f'"{key}"' in src, f"missing seed for {key}"


def test_upgrade_is_idempotent_via_on_conflict() -> None:
    """Re-running on a DB with live values must be a no-op (UPSERT pattern)."""
    src = _read()
    assert "ON CONFLICT (key) DO UPDATE" in src
    assert "CAST(:value AS jsonb)" in src


def test_downgrade_deletes_only_the_four_keys() -> None:
    """Downgrade must not touch any other system_config row."""
    src = _read()
    assert "DELETE FROM system_config" in src
    # The IN clause must enumerate exactly the four backported keys.
    for key in (
        "speculative_streaming_enabled",
        "grounding_check_async_enabled",
        "pipeline_parallel_output_guards_enabled",
        "grounding_check_threshold_by_intent",
    ):
        assert key in src
