"""[T2-CostPerf] Enable table_csv header+footer chunks default ON (Phase 5).

Revision ID: 010y
Revises: 010x
Create Date: 2026-05-25

Plan: 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 5 of 5.

Phase 1 shipped ``DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED=False``
so existing ingest paths stayed byte-identical. Phase 5 flips the
system-wide flag to ``true`` so any future re-ingest emits header +
footer synthetic chunks for mixed-CSV documents (intro paragraph +
CSV table + trailing notes).

Pre-conditions before applying:
  * Phase 1 ship (740a955 + adcf5d8 detector fix) on main
  * Phase 2 ship (046485a — alembic 010w few-shot prompt) applied
  * Phase 3 ship (4289687 — per-intent rerank top_n + context-cap, alembic 010x) applied
  * Server restarted to pick up Phase 3 wiring
  * Operator ready to re-ingest target documents (existing chunks NOT
    auto-migrated — only future ingest events emit the new chunk types)

Risk + rollback:
  * Per-bot opt-out: set ``plan_limits.table_csv_emit_header_footer_chunks_enabled``
    to ``false`` to preserve current behaviour for a specific bot.
  * Full rollback: ``alembic downgrade 010x`` flips system_config back
    to false; existing post-flip chunks are NOT removed (operator-driven
    re-ingest required if pre-flip behaviour must be restored on data
    already ingested under flag ON).

Idempotent: ``ON CONFLICT (key) DO UPDATE``.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010y"
down_revision: str | None = "010x"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Flip the table_csv header/footer flag to true system-wide."""
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'table_csv_emit_header_footer_chunks_enabled',
                'true'::jsonb,
                'bool',
                'Mixed-CSV doc: emit header chunk (intro + first rows) + footer chunk (last rows + trailing notes) alongside per-row chunks. Flipped ON 2026-05-25 after Phase 1-3 ship validation pass.',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                description = EXCLUDED.description,
                updated_at = NOW()
            """,
        ),
    )


def downgrade() -> None:
    """Revert to default OFF."""
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = 'false'::jsonb, updated_at = NOW()
            WHERE key = 'table_csv_emit_header_footer_chunks_enabled'
            """,
        ),
    )
