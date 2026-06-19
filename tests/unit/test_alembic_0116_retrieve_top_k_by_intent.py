"""Pin tests — alembic 0116 seeds per-intent retrieve top_k in system_config.

Guards against: revision drift, missing seed key, non-idempotent upgrade,
downgrade gap, value mismatch with the module-level constant.
"""

from __future__ import annotations

from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "_archive_pre_squash_20260618"
    / "20260526_0116_retrieve_top_k_by_intent.py"
)


def _read() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing migration: {_MIGRATION_PATH}"


def test_revision_chain() -> None:
    """0116 must chain off 0115."""
    src = _read()
    assert 'revision: str = "0116"' in src
    assert 'down_revision: str | None = "0115"' in src


def test_seed_value_matches_constant() -> None:
    """The JSON literal in the migration must contain all intents and values
    that match DEFAULT_RETRIEVE_TOP_K_BY_INTENT."""
    from ragbot.shared.constants import DEFAULT_RETRIEVE_TOP_K_BY_INTENT

    src = _read()
    # Each intent and its value must appear verbatim in the migration source.
    for intent, cap in DEFAULT_RETRIEVE_TOP_K_BY_INTENT.items():
        assert f'"{intent}":{cap}' in src or f'"{intent}": {cap}' in src, (
            f"migration missing seed for intent {intent!r} with cap {cap}"
        )


def test_idempotent_upsert() -> None:
    """Upgrade must use ON CONFLICT DO UPDATE so re-running is safe."""
    src = _read()
    assert "ON CONFLICT (key) DO UPDATE" in src
    assert "CAST(:val AS jsonb)" in src


def test_downgrade_deletes_key() -> None:
    """Downgrade must remove the seeded row and nothing else."""
    src = _read()
    assert "DELETE FROM system_config" in src
    assert "WHERE key = 'retrieve_top_k_by_intent'" in src


def test_seed_key_name_consistent() -> None:
    """The key name must be 'retrieve_top_k_by_intent' throughout."""
    src = _read()
    assert src.count("retrieve_top_k_by_intent") >= 3, (
        "key name must appear in INSERT, ON CONFLICT description, and DELETE"
    )


def test_all_canonical_intents_in_seed() -> None:
    """Every canonical intent must appear in the migration JSON."""
    src = _read()
    for intent in (
        "greeting", "chitchat", "vu_vo", "feedback", "out_of_scope",
        "factoid", "comparison", "multi_hop", "aggregation",
    ):
        assert f'"{intent}"' in src, f"intent {intent!r} missing from migration"
