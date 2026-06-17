"""[T1-Smartness] AdapChunk Wave C2 — seed parser_engine='kreuzberg' in system_config.

DB half of the constants.py flip in Wave C2 (commit pair
``[T1-Smartness] reorg(c2-kreuzberg-register)``). Seeds the
``system_config.parser_engine`` row so the runtime DB-override path matches
the new :data:`ragbot.shared.constants.DEFAULT_PARSER_ENGINE` (was
``"simple"``, now ``"kreuzberg"``). Runtime precedence:

    env ``RAGBOT_PARSER_ENGINE`` > system_config (DB) > constants.py default

Operators sync ``parser_engine`` to the worker's env var before (re)starting
the service — DI wiring is sync so the factory cannot read the DB during
container construction. Boot-time fallback chain (in factory) on missing
optional dep: ``kreuzberg`` → ``docling`` → ``simple``, so a stale image
without the kreuzberg wheel still boots cleanly with a warning.

Idempotent: ``ON CONFLICT (key) DO UPDATE`` so re-running on an already
seeded DB is a no-op (value/description refreshed). ``downgrade`` restores
the legacy ``"simple"`` value so the platform reverts to the pre-Wave-C2
behaviour.

Revision ID: 0097
Revises: 0096_l5_flag_on
Create Date: 2026-05-14

Renumbered from 0096 to 0097 during Wave K1 sequential merge to resolve
collision with Wave I1's 0096_l5_flag_on (both branches authored from
origin/main pre-A1/I1). Chain now: 0095 (cost_knobs) -> 0096_l5_flag_on
(I1) -> 0097 (kreuzberg parser_engine seed). No DDL semantic change.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0097"
down_revision = "0095a"
branch_labels = None
depends_on = None


_KEY = "parser_engine"
_NEW_VALUE = '"kreuzberg"'  # JSONB-encoded string
_LEGACY_VALUE = '"simple"'
_VALUE_TYPE = "string"
_DESCRIPTION = (
    "AdapChunk Layer 1 OCR parser engine. Options: kreuzberg|docling|simple. "
    "Operator must sync this value into worker env RAGBOT_PARSER_ENGINE before "
    "(re)starting the service; DI wiring is sync."
)


_UPSERT_SQL = text(
    """
    INSERT INTO system_config (key, value, value_type, description)
    VALUES (:key, (:value)::jsonb, :value_type, :description)
    ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value,
        value_type = EXCLUDED.value_type,
        description = EXCLUDED.description
    """
)


def upgrade() -> None:
    op.execute(
        _UPSERT_SQL.bindparams(
            key=_KEY,
            value=_NEW_VALUE,
            value_type=_VALUE_TYPE,
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        _UPSERT_SQL.bindparams(
            key=_KEY,
            value=_LEGACY_VALUE,
            value_type=_VALUE_TYPE,
            description=_DESCRIPTION,
        )
    )
