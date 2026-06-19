"""S2 cliff floor recalibration — pin constant + alembic migration + schema sync.

The S2 stream (2026-05-11) lowered ``DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR``
from 0.15 (alembic 0068, 2026-05-08) to 0.05. Empirical evidence:
``reports/LOADTEST_90Q_RESULT_20260511_161747.json`` shows the prior 0.15
floor contributes to ``REFUSE_GAP`` for Jina v3 + cross-encoder
distributions where legitimate Vietnamese queries score 0.05-0.20 on the
single best chunk.

These tests guard against silent drift between three sources of truth:

* The Python constant fallback (``shared/constants.py``).
* The alembic migration that seeds ``system_config.rerank_cliff_absolute_floor``.
* The ``PLAN_LIMIT_SCHEMA`` default that the bot-limit resolver returns
  when no per-bot override exists.

Sanity bounds: the floor stays inside the conservative ``[0.0, 0.20]``
window. A negative floor is a config bug; a floor above 0.20 would restore
the 0.15-era REFUSE_GAP regression on the same Jina v3 distribution.
"""

from __future__ import annotations

import re
from pathlib import Path

from ragbot.shared import bot_limits
from ragbot.shared.constants import DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR


_S2_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "_archive_pre_squash_20260618"
    / "20260511_0078_cliff_floor_recalibrate.py"
)


def test_default_cliff_floor_pin_at_zero_point_zero_five() -> None:
    """Pin the recalibrated S2 floor.

    Changing this value MUST also update the alembic migration body and the
    explanatory comment block in ``shared/constants.py`` — the 3-source sync
    rule from memory ``feedback_threshold_drift_post_migration``.
    """
    assert DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR == 0.05, (
        "S2 (2026-05-11) lowered the cliff floor from 0.15 → 0.05. The "
        "previous 0.15 calibration matched the threshold-strategy floor "
        "(alembic 0068) but Jina v3 + cross-encoder rerank produces a "
        "distribution where short Vietnamese queries score 0.05-0.20 on "
        "legitimate chunks. If this assertion fails the constant drifted; "
        "verify the alembic 0078 migration value matches before changing."
    )


def test_cliff_floor_inside_sanity_window() -> None:
    """Hard sanity bounds for the floor.

    Negative → config bug (drops nothing meaningfully — every score >= 0).
    Above 0.20 → restores the REFUSE_GAP regression S2 was calibrated to
    fix; the upper bound is intentionally tight so future tuning surfaces
    here before silently regressing live retrieval.
    """
    assert 0.0 <= DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR <= 0.20, (
        "Cliff floor must stay in [0.0, 0.20] until S2 calibration is "
        f"explicitly revisited; got {DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR}."
    )


def test_plan_limit_schema_default_matches_constant() -> None:
    """``PLAN_LIMIT_SCHEMA`` must not drift from the constant fallback."""
    schema = bot_limits.PLAN_LIMIT_SCHEMA["rerank_cliff_absolute_floor"]
    assert schema["default"] == DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR
    assert schema["type"] == "float"
    assert schema["min"] == 0.0
    assert schema["max"] == 1.0


def test_alembic_migration_seeds_calibrated_floor() -> None:
    """The S2 alembic migration body must encode the same 0.05 value.

    Tests parse the migration source to guarantee a row update will fire on
    upgrade. We assert both the config key and the new value literal so a
    silent edit to either side is caught at unit-test time (before live
    DB drift).
    """
    src = _S2_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "rerank_cliff_absolute_floor" in src, (
        "S2 alembic migration must target the rerank_cliff_absolute_floor "
        "key in system_config."
    )
    # Match the literal '0.05' inside a value= bindparam — a value-only
    # match would also accept the docstring mention.
    assert re.search(r'value\s*=\s*"0\.05"', src), (
        "S2 alembic migration must seed the 0.05 floor. If S2 floor is "
        "re-tuned, update the constant + this test together (3-source sync)."
    )
    # Downgrade path must restore 0.15 (alembic 0068 era).
    assert "0.15" in src, (
        "S2 alembic migration downgrade path must restore the prior 0.15 "
        "floor so rollback is symmetric."
    )


def test_alembic_migration_metadata() -> None:
    """The S2 migration revision chains correctly off the most recent head."""
    src = _S2_MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'revision = "0078"' in src
    assert 'down_revision = "0077"' in src
