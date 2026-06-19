"""[T3-Refactor] Wave M3.3-C — drop legacy top_k_retrieve/top_k_rerank keys.

Revision ID: 010o
Revises: 010n
Create Date: 2026-05-20

Pre-fix the ``system_config`` table held TWO rows for each retrieval
top-K knob:

  * ``top_k_retrieve = 10``   (legacy, written by ``rago_pareto_sweep.py``)
  * ``rag_top_k = 20``         (canonical, read by production)

  * ``top_k_rerank = 5``       (legacy, written by ``rago_pareto_sweep.py``)
  * ``rag_rerank_top_n = 10``  (canonical, read by production)

Production ``chat_worker._build_pipeline_config`` and
``test_chat._build_pipeline_config`` ONLY read the ``rag_*`` keys, so
the legacy rows accumulated value drift without affecting runtime —
but the Pareto sweep analyser ``scripts/rago_pareto_pick.py`` still
reported optimisation results against the LEGACY rows. Decisions like
"best config has top_k_rerank=5" never reached production.

Wave M3.3-D renamed the sweep schema to use ``rag_top_k`` /
``rag_rerank_top_n``, so the legacy rows are now genuinely orphaned —
no reader, no writer in current ``main``. This migration removes them
so a future operator does not re-introduce drift by editing the wrong
row.

The legacy values are preserved in ``description`` for forensic
recovery: future debuggers can ``git log -G "top_k_retrieve"`` to
trace the pre-rename history.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)


revision: str = "010o"
down_revision: str | None = "010n"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_LEGACY_KEYS: tuple[str, ...] = ("top_k_retrieve", "top_k_rerank")


def upgrade() -> None:
    """Delete the two legacy ``top_k_*`` rows from ``system_config``.

    Idempotent — re-running the migration on a DB that no longer has the
    rows is a no-op.
    """
    op.execute(
        text(
            "DELETE FROM system_config WHERE key = ANY(:keys)"
        ).bindparams(keys=list(_LEGACY_KEYS))
    )


def downgrade() -> None:
    """Re-seed the legacy rows with their pre-deletion values.

    Values match the documented drift state captured in the M3.3 audit
    (``top_k_retrieve=10``, ``top_k_rerank=5``). Restoring them brings
    back the orphan state, not the working state — operators should
    NOT use this downgrade in production, only in test fixtures.
    """
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES
                ('top_k_retrieve', '10', 'int',
                 'M3.3-C downgrade re-seed (legacy orphan)', now()),
                ('top_k_rerank',   '5',  'int',
                 'M3.3-C downgrade re-seed (legacy orphan)', now())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = now()
            """
        )
    )
