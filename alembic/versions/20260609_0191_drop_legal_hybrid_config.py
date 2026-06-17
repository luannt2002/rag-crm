"""Drop the legal-hybrid config keys — feature removed (domain-neutral revert).

Revision: 0191
Prev:     0190

The legal-hybrid chunking experiment (born + reverted same day, 2026-06-08/09)
failed its clean A/B (reproducible HALLU=0 breach, no real Coverage lift) AND
violated the platform's domain-neutral + multi-bot mindset (a feature named
after the "legal" domain that chased a single bot). The code has been removed in
the accompanying commit; this migration deletes the now-orphan ``system_config``
rows so no dead, domain-named knobs linger in the DB.

The correct lever for multi-fact drop-fact is the GENERAL generation-layer
structured sub-answer (``structured_subanswer_enabled``, kept) which applies to
EVERY bot, not per-content-type chunking.

Idempotent. Reversible only as a no-op (the removed code would not read the keys
back); downgrade is intentionally empty.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0191"
down_revision: str | None = "0190"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_KEYS = ("adapchunk_legal_hybrid_enabled", "adapchunk_legal_hybrid_min_words")


def upgrade() -> None:
    op.execute(
        text("DELETE FROM system_config WHERE key = ANY(:keys)").bindparams(keys=list(_KEYS))
    )


def downgrade() -> None:
    # Feature removed from code — re-seeding the keys would have no reader.
    pass
