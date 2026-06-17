"""Wave I (AdapChunk-reorg) — seed AdapChunk Layer 5 cross-check flag ON.

Flips the ``adapchunk_layer5_cross_check_enabled`` ``system_config`` row to
``true`` so the 5-rule post-selector override (merged via sprint3-l5-crosscheck
``a6ff98a``) becomes the production default. Constant fallback at
:data:`ragbot.shared.constants.DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED`
is flipped in the same wave; this migration just synchronises the DB row
so live ops do not need to manually toggle.

The 5 cross-check conditions (already shipped in
:func:`ragbot.shared.chunking.apply_cross_check`):

1. ``confidence < threshold`` → ``hybrid`` defensive fallback.
2. ``hdt`` picked but ``heading_count < min`` → downgrade to ``semantic``.
3. ``semantic`` picked but avg block length too short → ``proposition``.
4. ``proposition`` picked but blocks long + heading-rich → upgrade to ``hdt``.
5. ``mixed_content_ratio`` high and not already ``hybrid`` → warning only.

Idempotent: ``ON CONFLICT (key) DO UPDATE`` lets re-runs refresh the value.
``downgrade`` flips the row back to ``false`` (defensive — the constant
default also flips back if reorg is reverted).

Renumbered from 0095_l5_flag_on to 0096_l5_flag_on during Wave K1
sequential merge to chain after 0095 (cost_knobs already seeds the same
adapchunk_layer5_cross_check_enabled=true row — this migration is now
idempotent re-affirm of that value).
"""

from __future__ import annotations

from alembic import op

revision = "0095a"
down_revision = "0095"
branch_labels = None
depends_on = None


_KEY = "adapchunk_layer5_cross_check_enabled"
_DESC = (
    "AdapChunk Layer 5 — post-selector 5-rule cross-check active by default "
    "(Wave I AdapChunk-reorg flip; code shipped sprint3-l5-crosscheck a6ff98a)."
)


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO system_config (key, value, value_type, description)
        VALUES ('{_KEY}', 'true', 'bool', '{_DESC}')
        ON CONFLICT (key) DO UPDATE
            SET value = 'true',
                value_type = 'bool',
                description = EXCLUDED.description;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE system_config
           SET value = 'false'
         WHERE key = '{_KEY}';
        """
    )
