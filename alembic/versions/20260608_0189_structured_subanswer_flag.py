"""Seed ``structured_subanswer_enabled`` platform flag (default OFF).

Revision: 0189
Prev:     0188

Trigger (2026-06-08 S2 — generation structured sub-answer):
  The generate node emits a flat ``GenerateOutput.answer`` string. For
  multi-fact intents (aggregation / comparison / list / multi_hop) a flat
  single-string answer drops facts when the question spans several corpus
  rows. The fix adds an OPTIONAL reasoning-first structured path where the
  model enumerates each facet in ``sub_answers`` BEFORE composing the final
  ``answer`` (SHAPE only — no answer-text injection, no post-edit).

  This platform default is OFF (rule #0: no unmeasured default change). The
  orchestrator reads ``structured_subanswer_enabled`` via pipeline_config
  with a literal ``False`` fallback; flipping this key ON (or a per-bot
  override) enables the structured schema for the gated multi-fact intents.

Sacred-rule alignment:
  ✅ Pure alembic INSERT (CLAUDE.md rule 7) — no psql hot-fix
  ✅ Zero-hardcode (default value lives in DB, not inline behaviour toggle)
  ✅ Default OFF (rule #0 — A/B validates before any default flip)
  ✅ Multi-tenant (platform default; per-bot override via pipeline_config)
  ✅ Idempotent (ON CONFLICT DO NOTHING preserves operator edits)
  ✅ Reversible (downgrade deletes the seeded key)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0189"
down_revision: str | None = "0188"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_FLAG_KEY = "structured_subanswer_enabled"
_FLAG_VALUE = "false"  # default OFF — rule #0 (no unmeasured default change)
_FLAG_TYPE = "bool"
_FLAG_DESC = (
    "When true, the generate node requests the structured sub-answer schema "
    "(GenerateOutput.sub_answers reasoning-first array) for multi-fact "
    "intents (aggregation / comparison / multi_hop) so the model enumerates "
    "each facet before composing the final answer. Factoid / social intents "
    "keep the lean flat schema. SHAPE only — no answer-text injection. "
    "Default false; per-bot override via pipeline_config."
)


def upgrade() -> None:
    """Seed the structured-sub-answer flag (default OFF)."""
    op.get_bind().execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:k, CAST(:v AS jsonb), :t, :d, NOW())
            ON CONFLICT (key) DO NOTHING
            """,
        ),
        {"k": _FLAG_KEY, "v": _FLAG_VALUE, "t": _FLAG_TYPE, "d": _FLAG_DESC},
    )


def downgrade() -> None:
    """Remove the seeded flag (preserves any operator override only if absent)."""
    op.get_bind().execute(
        text("DELETE FROM system_config WHERE key = :k"),
        {"k": _FLAG_KEY},
    )
