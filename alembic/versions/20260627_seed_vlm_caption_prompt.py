"""Seed the operator-tunable VLM caption-prompt system_config key (domain-neutral).

The VLM image parser (multimodal Phase 2) sends a caption instruction to the
vision model when an image-only upload is ingested. That instruction used to be
a hardcoded Vietnamese string inside the parser source — a sacred #10 violation
(the platform must not hardcode/inject the text it asks the LLM to produce) plus
a domain-neutral / zero-hardcode breach (fixed user-language literal in .py).

The text is now config-owned: ``system_config.vlm_caption_prompt``. When the key
is absent the parser falls back to the domain-neutral platform default constant
(``DEFAULT_VLM_CAPTION_PROMPT``). This migration seeds the row with that same
English, language-agnostic default so an operator can discover and edit it via the
admin UI (audit-trailed) without a redeploy — the vision model still captions in
the image's own language regardless of the instruction language.

Idempotent ON CONFLICT (key) DO NOTHING so a re-run never clobbers an operator's
later override.

Revision ID: seed_vlm_caption_prompt_260627
Revises: seed_anti_fabricate_rule_260627
Create Date: 2026-06-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from ragbot.shared.constants import DEFAULT_VLM_CAPTION_PROMPT

revision = "seed_vlm_caption_prompt_260627"
down_revision = "seed_anti_fabricate_rule_260627"
branch_labels = None
depends_on = None

_KEY = "vlm_caption_prompt"
_DESCRIPTION = (
    "Domain-neutral caption instruction sent to the vision model when ingesting "
    "an image-only upload (vlm_provider != null). Operator/owner-owned; edit via "
    "admin UI. HALLU=0: mirror the image content, forbid fabrication."
)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description)
            VALUES (:key, to_jsonb(CAST(:value AS text)), :value_type, :description)
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(
            key=_KEY,
            value=DEFAULT_VLM_CAPTION_PROMPT,
            value_type="str",
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    """Remove the seeded key -> parser falls back to the constant default."""
    op.execute(
        text("DELETE FROM system_config WHERE key = :key").bindparams(key=_KEY)
    )
