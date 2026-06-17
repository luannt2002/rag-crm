"""Disable speculative_streaming_enabled until Phase 3 verifier ships.

Revision: 0119
Prev:      0118

Bug #3 fix — alembic 0115 set ``speculative_streaming_enabled = true`` but
the SpeculativeRouter was broken (Bug #2: async generator vs coroutine
TypeError).  More critically, commit ``8fd784f`` explicitly stated that the
flag must remain ``false`` until Phase 3's HALLU verifier ships (default OFF
is mandatory to preserve HALLU=0 sacred contract).

Alembic 0115 backported the production-psql value (which was set manually and
never verified against the Phase 3 readiness gate) → CRITICAL violation of
the commit mandate.

This migration corrects the drift by setting the flag back to ``false``.
Once Phase 3 HALLU verifier ships and Bug #2 fix is end-to-end load-tested,
a separate migration can flip it to ``true`` with explicit Phase 3 gate sign-off.

HALLU=0 sacred impact:
    With the flag ``true`` (broken state): streaming callers hit TypeError
    inside SpeculativeRouter → pipeline crash → empty ``done`` event.
    With the flag ``false`` (correct state): streaming callers fall through
    to single-LLM path in ``query_graph.py`` (line 1158 ``_stream_sink``
    guard) — full RAG pipeline, grounded answer, HALLU=0 preserved.
"""

from alembic import op
from sqlalchemy import text

revision: str = "0119"
down_revision: str | None = "0118"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Set speculative_streaming_enabled = false (Phase 3 not shipped)."""
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'speculative_streaming_enabled',
                CAST('false' AS jsonb),
                'bool',
                'Phase 2 default OFF — Phase 3 HALLU verifier must ship before enabling. '
                'See Bug #2 fix (speculative_router.py async-generator race redesign).',
                NOW()
            )
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = NOW()
            """
        )
    )


def downgrade() -> None:
    """Restore speculative_streaming_enabled = true (pre-fix state).

    WARNING: Do NOT run downgrade on production — this restores the broken
    state (Bug #2 TypeError + Phase 3 gate violation).
    """
    op.execute(
        text(
            """
            UPDATE system_config
            SET value = CAST('true' AS jsonb),
                description = 'Wave M3.6 F1 — Apple-paper speculative streaming TTFB optimisation.',
                updated_at = NOW()
            WHERE key = 'speculative_streaming_enabled'
            """
        )
    )
