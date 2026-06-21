"""Enable vision on gpt-4.1-mini for multimodal VLM captioning.

The gpt-4.1 family is multimodal; the model row was seeded with supports_vision=false.
Multimodal Phase 2 reads this flag to gate the VLM image parser (a multipart image
message is only sent to a model where supports_vision=true). Flip it on for the
captioner model. Content/config state change via tracked alembic (CLAUDE.md: no psql).

Revision ID: enable_vision_gpt41_20260621
Revises: backfill_stats_chunk_fk_20260621
"""
from __future__ import annotations

from alembic import op

revision = "enable_vision_gpt41_20260621"
down_revision = "backfill_stats_chunk_fk_20260621"
branch_labels = None
depends_on = None

_MODEL = "gpt-4.1-mini"


def upgrade() -> None:
    op.execute(
        "UPDATE ai_models SET supports_vision = true "
        f"WHERE name = '{_MODEL}'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE ai_models SET supports_vision = false "
        f"WHERE name = '{_MODEL}'"
    )
