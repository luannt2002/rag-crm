"""[T3-Refactor] Cleanup qwen3.6-35b-kimi orphan row from ai_models.

Revision ID: 010u
Revises: 010t
Create Date: 2026-05-21

Pre-condition: a row for
``qwen3.6-35b-a3b-kimi-k2.6-reasoning-distilled`` was inserted ad-hoc
during the 2026-05-21 evening test session — the LLM-answer binding
on the legalbot was temporarily swapped to this model to probe whether
a reasoning-class LM Studio model can drive the realtime Ragbot
pipeline. Verdict: NO. Real-pipeline measurements (1499000-aggregation
testcase + 6Q smoke) showed:

* Per-turn latency 30–56 s (7–10 LLM calls × forced reasoning chain).
* Structured-output JSON empty when ``max_tokens`` is consumed by the
  reasoning chain before the model can flush a clean JSON block.
* LM Studio does NOT honour ``enable_thinking=false`` / ``/no_think``
  prompt prefix; the chain-of-thought is unavoidable.

Cost-saving claim ($200–400/month) is gated by a 30-question HALLU
trap dataset that was never collected before the test ended. Until
that gate passes, this model is unsuitable for ``llm_primary``.

The binding was rolled back to ``gpt-4.1-mini`` after the smoke test,
but the model row was left active in ``ai_models`` (no orchestrator
references it, so it is now an orphan). This migration soft-deletes
the row so audit / admin tooling does not surface a usable model that
production declined.

Idempotent: ``WHERE deleted_at IS NULL`` guard. Re-running on an
already-cleaned row is a no-op.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010u"
down_revision: str | None = "010t"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Soft-delete the qwen3.6-35b-kimi row.

    Scoped to the innocom_lmstudio provider so a future row of the same
    model name from a different vendor (unlikely but defensive) cannot
    be accidentally hidden.
    """
    op.execute(
        text(
            """
            UPDATE ai_models SET
                deleted_at = NOW(),
                enabled = false,
                updated_at = NOW()
            WHERE name = 'qwen3.6-35b-a3b-kimi-k2.6-reasoning-distilled'
              AND record_provider_id = (
                  SELECT id FROM ai_providers WHERE name = 'innocom_lmstudio' LIMIT 1
              )
              AND deleted_at IS NULL
            """,
        ),
    )


def downgrade() -> None:
    """Restore active state on the qwen3.6-35b-kimi row.

    Defensive: an operator with reason to re-test the model (e.g. after
    LM Studio upgrades the reasoning-toggle support) can downgrade this
    revision to recover the row instead of re-inserting from scratch.
    """
    op.execute(
        text(
            """
            UPDATE ai_models SET
                deleted_at = NULL,
                enabled = true,
                updated_at = NOW()
            WHERE name = 'qwen3.6-35b-a3b-kimi-k2.6-reasoning-distilled'
              AND record_provider_id = (
                  SELECT id FROM ai_providers WHERE name = 'innocom_lmstudio' LIMIT 1
              )
            """,
        ),
    )
