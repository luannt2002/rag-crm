"""Raise innocom LLM provider timeout 30s → 90s (perf: stop timeout-truncation).

Load-test 2026-07-08 (200q): the innocom chat endpoint is slow (3-30s/call,
occasionally more) and the 30s provider timeout was CUTTING answers mid-generation
— a price-lookup started correctly ("Lốp ROVELO 155/70R13…") then got truncated,
and 16/200 came back empty (the generate call cancelled before finishing). That is
a SPEED artifact, not a wrong answer. Owner decision: give innocom 90s to finish.

Trade-off (documented, not changed here): retry_policy max_attempts=3 stacks, so a
call that still times out at 90s can retry up to 3× (worst-case 270s); and a heavy
multi-hop turn runs 3-5 sequential LLM calls. Raising the timeout closes the
single-answer TRUNCATION (correctness); heavy-path latency still needs a faster
endpoint (external). Reducing retries is a separate follow-up if worst-case latency
matters more than completeness.

Sacred #7: ai_providers content change via TRACKED alembic (never psql UPDATE).
Idempotent (guarded on current value) + reversible (downgrade restores 30s).

Revision ID: innocom_timeout_90s_260708
Revises: empty_guard_bots_260708
"""
from __future__ import annotations

from alembic import op

revision = "innocom_timeout_90s_260708"
down_revision = "empty_guard_bots_260708"
branch_labels = None
depends_on = None

_NEW_MS = 90000
_OLD_MS = 30000


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE ai_providers
        SET timeout_ms = {_NEW_MS}
        WHERE name = 'innocom' AND timeout_ms = {_OLD_MS}
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE ai_providers
        SET timeout_ms = {_OLD_MS}
        WHERE name = 'innocom' AND timeout_ms = {_NEW_MS}
        """
    )
