"""[T3-Refactor] Hot-fix gemma-4-e2b-it deleted_at inconsistency.

Revision ID: 010t
Revises: 010s
Create Date: 2026-05-21

Pre-condition: ``gemma-4-e2b-it`` row was soft-deleted by an alembic 010s
downgrade (2026-05-21 12:22:27) during a mid-session rollback (Innocom
swap plan v2 failed; plan v3 shipped after a routing-pattern fix). The
subsequent 010s re-upgrade did NOT reset ``deleted_at = NULL`` — the
``ON CONFLICT DO UPDATE`` clause was missing that one column — leaving
the active ``bot_model_bindings`` rows (grading + grounding) pointing
to a model row whose ``deleted_at`` is non-null.

Symptom: ``SELECT name, deleted_at FROM ai_models WHERE name =
'gemma-4-e2b-it'`` returns a timestamped row, while
``SELECT * FROM bot_model_bindings WHERE record_model_id = <gemma_id>
AND active = true AND deleted_at IS NULL`` returns active rows.

Why production still works: LiteLLM router caches model_id directly;
the resolver does NOT filter on ``ai_models.deleted_at``. So the
inconsistency is silent today, but a future cleanup migration
(``DELETE FROM ai_models WHERE deleted_at IS NOT NULL``) would purge
the row and break grading + grounding service-side.

Fix: restore ``deleted_at = NULL`` for the actively-used Innocom row.
The 010s file itself has been edited to include ``deleted_at = NULL``
in the ON CONFLICT clause so the same accident cannot recur.

Idempotent: guarded by ``WHERE deleted_at IS NOT NULL`` — re-running
is a no-op once the row is fixed.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010t"
down_revision: str | None = "010s"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Reset deleted_at = NULL for the gemma-4-e2b-it row that bindings
    still point at, AND restore the innocom_lmstudio provider row itself
    (also soft-deleted by the same rollback transaction).

    Scoped via provider code='custom_openai' instead of deleted_at filter
    because the provider row was soft-deleted simultaneously by the same
    failed 010s downgrade — a strict ``deleted_at IS NULL`` filter would
    match zero rows and silently no-op.
    """
    # 1. Restore the innocom_lmstudio provider row first so downstream
    # joins see a live row.
    op.execute(
        text(
            """
            UPDATE ai_providers SET
                deleted_at = NULL,
                enabled = true,
                updated_at = NOW()
            WHERE name = 'innocom_lmstudio'
              AND deleted_at IS NOT NULL
            """,
        ),
    )
    # 2. Restore the gemma-4-e2b-it model row that legalbot bindings
    # depend on. Subquery now finds the live provider.
    op.execute(
        text(
            """
            UPDATE ai_models SET
                deleted_at = NULL,
                enabled = true,
                updated_at = NOW()
            WHERE name = 'gemma-4-e2b-it'
              AND record_provider_id = (
                  SELECT id FROM ai_providers WHERE name = 'innocom_lmstudio' LIMIT 1
              )
              AND deleted_at IS NOT NULL
            """,
        ),
    )


def downgrade() -> None:
    """No-op intentionally.

    The pre-fix state (deleted_at set + active bindings) is itself the
    bug — there is no value in restoring it. Operators rolling back
    past this migration should also roll back 010s (the source of the
    inconsistency) and start clean.
    """
    pass
