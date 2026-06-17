"""Seed ``crag_grader_provider`` system_config row for CRAG batch grader ship.

Stream S3 (master DeepDive Finding #19): CRAG grading historically ran one
structured LLM call per chunk (up to ``top_k=50`` calls per turn). The
Port + Strategy + Registry refactor (``ragbot.application.services.crag_grader``)
exposes three strategies — ``per_chunk`` (legacy default), ``batch``
(single call grading every chunk via ``GradeBatchOutput`` schema), and
``null`` (Null Object; every chunk scored 1.0 for emergency disable).

This migration UPSERTs the default ``per_chunk`` provider so existing
deployments inherit the same N-call behaviour they ran before the
abstraction layer landed. Operators flip to ``"batch"`` after the
admin's A/B golden-set verifies faithfulness preserved (±2%) and cost
drops as expected. The companion knob ``crag_batch_grader_max_chunks``
caps the per-call window so a rogue ``top_k=500`` request cannot
overflow the LLM context budget.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running the migration
on a DB already at the new value is a no-op.

Revision ID: 0079
Revises: 0077
Create Date: 2026-05-11

Note on revision numbering: Stream S1 (prefix-pollution-fix) holds 0078;
this Stream S3 migration takes 0079. Both ``down_revision`` point at
0077 (the post-90Q-loadtest anchor) — they are independent siblings.
When admin cherry-picks selectively, Alembic's branch resolution
collapses cleanly because each migration touches a different
``system_config`` row set (S1 = ``embedding_text_strategy``; S3 =
``crag_grader_provider`` + ``crag_batch_grader_max_chunks``).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0079a"
down_revision = "0079"
branch_labels = None
depends_on = None


_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "crag_grader_provider",
        "per_chunk",
        "string",
        "CRAG grader strategy (Port + Registry). Values: per_chunk "
        "(legacy N-call, default), batch (single structured-output call, "
        "~10-50x cheaper for top_k>=10), null (Null Object, every chunk "
        "scored 1.0 — emergency disable). Flip to 'batch' after A/B "
        "golden-set verifies faithfulness preserved (master DeepDive "
        "Finding #19).",
    ),
    (
        "crag_batch_grader_max_chunks",
        "50",
        "int",
        "Ceiling on chunks per batched LLM grade call. BatchCragGrader "
        "slices oversize input into sequential windows so a rogue "
        "top_k=500 request cannot exceed the LLM context budget. Default "
        "matches ``DEFAULT_CRAG_BATCH_GRADER_MAX_CHUNKS`` (50).",
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _TUNING_ROWS:
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
    """Remove the CRAG grader provider rows.

    Safe: when the rows are absent the resolver falls back to
    ``DEFAULT_CRAG_GRADER_PROVIDER`` (= ``per_chunk``) at runtime, so
    behaviour is identical to the migrated state.
    """
    op.execute(
        text(
            "DELETE FROM system_config "
            "WHERE key IN ('crag_grader_provider', 'crag_batch_grader_max_chunks')"
        )
    )
