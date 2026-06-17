"""Seed ``system_config`` keys for Tier 2 action state framework.

Revision: 0150b (chained after 0150 schema migration)
Prev:     0150

Trigger (2026-05-30 X2 BUNDLED ship step 4):
  Zero-hardcode sacred rule requires platform defaults live in
  ``system_config`` (Redis-cached DB), not inline in code. Tier 2 adds
  3 platform-level defaults:

  1. ``conversation_state_provider`` (string) — Registry strategy name
     for ``ConversationStatePort``. Default "null" (Null Object → bot
     opt-in switches to "jsonb" via per-bot config).

  2. ``slot_extractor_model`` (string) — LLM model used by
     ``SlotExtractor`` service. Per memory ``feedback_haiku_partial_only``
     Haiku is correct tier for token-small extraction.

  3. ``action_state_drift_threshold`` (float) — Confidence threshold
     for drift_detection.severity="block" raise. Owner override per-bot
     via ``bots.action_config.drift_detection.threshold``.

Sacred-rule alignment:
  ✅ Pure alembic INSERT (CLAUDE.md rule 7)
  ✅ Zero-hardcode (values in DB, not code)
  ✅ Multi-tenant (platform defaults; per-bot override via plan_limits)
  ✅ Idempotent (ON CONFLICT DO NOTHING preserves operator edits)
  ✅ Reversible (downgrade deletes seeded keys)
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0150b"
down_revision: str | None = "0150"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_SEED_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "conversation_state_provider",
        '"null"',
        "string",
        "Registry strategy for ConversationStatePort: 'null' (default OFF, "
        "Null Object pattern) or 'jsonb' (DB-backed). Per-bot opt-in via "
        "bots.action_config.enabled=true switches to jsonb automatically.",
    ),
    (
        "slot_extractor_model",
        '"haiku"',
        "string",
        "LLM model alias for SlotExtractor (token-small JSON-mode extraction). "
        "Haiku per memory feedback_haiku_partial_only (correct tier for "
        "token-small enrichment workloads).",
    ),
    (
        "action_state_drift_threshold",
        "0.95",
        "float",
        "Min similarity confidence for drift_detection.severity='block' raise. "
        "Below threshold = severity='warn' (log + emit guardrail_flags). "
        "Per-bot override via bots.action_config.drift_detection.threshold.",
    ),
)


def upgrade() -> None:
    """Seed platform defaults for Tier 2 action state."""
    conn = op.get_bind()
    for key, value, vtype, desc in _SEED_ROWS:
        conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description, updated_at)
                VALUES (:k, CAST(:v AS jsonb), :t, :d, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
            ),
            {"k": key, "v": value, "t": vtype, "d": desc},
        )


def downgrade() -> None:
    """Remove seeded keys (preserves operator overrides)."""
    op.execute(
        text(
            """
            DELETE FROM system_config
            WHERE key IN (
                'conversation_state_provider',
                'slot_extractor_model',
                'action_state_drift_threshold'
            )
            """,
        ),
    )
