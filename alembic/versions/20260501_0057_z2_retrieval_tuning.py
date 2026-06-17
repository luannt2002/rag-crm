"""Z2-RETRIEVAL-MAX — bump retrieval defaults for top_score lift.

Aligns live ``system_config`` with the new constants in ``shared/constants.py``
shipped under AGENT-Z2-RETRIEVAL-MAX (2026-05-01):

* ``rag_rerank_top_n`` 5 → 7 — wider answer-context window into generate.
* ``multi_query_n_variants`` (insert) 5 — Vietnamese paraphrase coverage.
* ``multi_query_max_variants`` (insert) 7 — headroom over new default.
* ``ef_search`` (insert) 100 — broader HNSW candidate pool at query time.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running this migration on a
DB that already received hot-fixed values keeps the new value (UPSERT style).
Operators who deliberately tuned a key per-deploy must re-tune via
``system_config`` after running this migration.

Revision ID: 0057
Revises: 0056
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


# (key, value, type, description) — matches the 4-column shape used by
# ``init_system_config.py`` so the same loader path is exercised.
_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "rag_rerank_top_n",
        "7",
        "int",
        "Z2-RETRIEVAL-MAX: top_n into generate (5→7 for multi-fact coverage).",
    ),
    (
        "multi_query_n_variants",
        "5",
        "int",
        "Z2-RETRIEVAL-MAX: paraphrase fan-out (3→5 for VN morphology).",
    ),
    (
        "multi_query_max_variants",
        "7",
        "int",
        "Z2-RETRIEVAL-MAX: hard ceiling raised (5→7) — per-bot headroom.",
    ),
    (
        "ef_search",
        "100",
        "int",
        "Z2-RETRIEVAL-MAX: HNSW query-time candidate pool (80→100).",
    ),
)


def upgrade() -> None:
    """Upsert retrieval tuning keys into ``system_config``."""
    for key, value, value_type, description in _TUNING_ROWS:
        # ``description`` carries the change reason so operators reading the
        # table see why the value diverges from a stale init_system_config.py.
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, :value, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    """Restore the prior live values (matched against actual prod state).

    ``ef_search`` and the two ``multi_query_*`` keys did not exist before this
    migration in live DB — DELETE removes them so the constant fallback wins
    again. ``rag_rerank_top_n`` had value=5 → revert.
    """
    op.execute(
        text(
            "UPDATE system_config SET value = '5' "
            "WHERE key = 'rag_rerank_top_n'"
        )
    )
    op.execute(
        text(
            "DELETE FROM system_config "
            "WHERE key IN ('multi_query_n_variants', 'multi_query_max_variants', 'ef_search')"
        )
    )
